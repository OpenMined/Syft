import asyncio
import json
import secrets
import shutil
from pathlib import Path

import pytest
from loguru import logger

from tests.e2e.conftest import Client, E2EContext, Server

PROJECT_NAME = "FedMNIST"

AGGREGATOR_CONFIG = {
    "project_name": PROJECT_NAME,
    "aggregator": "agg@openmined.org",
    "participants": ["user1@openmined.org", "user2@openmined.org"],
    "model_arch": "model.py",
    "model_class_name": "FLModel",
    "model_weight": "global_model_weight.pt",
    "test_dataset": "mnist_test_dataset.pt",
    "rounds": 3,
    "epoch": 10,
    "learning_rate": 0.1,
}
MAX_COPY_PVT_DATASETS = 3


def deployment_config():
    return {
        "e2e_name": "fl_model_training",
        "server": Server(port=5001),
        "clients": [
            Client(
                name="agg",
                port=8080,
                apps=["https://github.com/OpenMined/fl_aggregator"],
            ),
            Client(
                name="user1",
                port=8081,
                server_port=5001,
                apps=[
                    "https://github.com/OpenMined/fl_client",
                ],
            ),
            Client(
                name="user2",
                port=8082,
                server_port=5001,
                apps=[
                    "https://github.com/OpenMined/fl_client",
                ],
            ),
        ],
    }


async def copy_private_data(e2e_client: E2EContext, client: Client):
    logger.info(f"Copying private data to FL client: {client.email}")
    await e2e_client.wait_for_api("fl_client", client)
    private_data_dir = client.data_dir / "private" / "fl_client"
    private_data_dir.mkdir(parents=True, exist_ok=True)

    # sample private data
    sample_private_data_dir = client.api_path("fl_client") / "mnist_samples"
    sample_private_data_files = list(sample_private_data_dir.glob("*.pt"))

    for _ in range(MAX_COPY_PVT_DATASETS):
        idx = secrets.randbelow(len(sample_private_data_files))
        mnist_data_file = sample_private_data_files[idx]
        logger.debug(f"Copying {mnist_data_file.resolve()} to {client.email} private dir")
        shutil.copy(mnist_data_file, private_data_dir)
        assert Path(private_data_dir, mnist_data_file.name).exists()

    logger.info(f"Private data successfully added for FL client: {client.email}")


async def check_fl_client_installed(e2e_client: E2EContext, client: Client):
    """Check if FL client is installed and running"""
    logger.info(f"Checking if FL client is installed for {client.email}")
    fl_client_dir = client.api_data_dir("fl_client")

    # App is installed in api_data_dir
    await e2e_client.wait_for_path(fl_client_dir, timeout=30)

    # Check if request, running and done folders are created
    await e2e_client.wait_for_path(fl_client_dir / "request", timeout=30)
    await e2e_client.wait_for_path(fl_client_dir / "running", timeout=30)
    await e2e_client.wait_for_path(fl_client_dir / "done", timeout=30)

    logger.info(f"FL client installed for {client.email}")


async def approve_data_request(e2e_client: E2EContext, client: Client):
    """Approve data request for FL client"""

    logger.info(f"Approving data request for {client.email}")
    request_dir = client.api_data_dir("fl_client") / "request"
    project_dir = request_dir / PROJECT_NAME

    await e2e_client.wait_for_path(project_dir, timeout=30, interval=1)
    assert project_dir.exists()

    running_dir = client.api_data_dir("fl_client") / "running"

    # Approve request
    # Approve action is moving project dir to running dir
    shutil.move(project_dir, running_dir)

    # Ensure fl_config.json is present in running dir
    await e2e_client.wait_for_path(
        running_dir / PROJECT_NAME / "fl_config.json",
        timeout=90,
    )
    
    # Ensure model_arch is present in running dir
    await e2e_client.wait_for_path(
        running_dir / PROJECT_NAME / AGGREGATOR_CONFIG["model_arch"],
        timeout=90,
    )

    assert Path(running_dir / PROJECT_NAME).exists()
    assert Path(running_dir / PROJECT_NAME / "fl_config.json").exists()
    logger.info(f"Data request approved for {client.email}")


async def check_for_training_complete(e2e_client: E2EContext, client: Client):
    logger.info(f"Checking for training completion for {client.email}")
    done_dir = client.api_data_dir("fl_client") / "done"
    assert done_dir.exists()

    await e2e_client.wait_for_path(done_dir / PROJECT_NAME, timeout=300, interval=1)

    agg_weights_dir = done_dir / PROJECT_NAME / "agg_weights"
    round_weights_dir = done_dir / PROJECT_NAME / "round_weights"

    assert agg_weights_dir.exists()
    assert round_weights_dir.exists()

    assert len(list(round_weights_dir.glob("*.pt"))) == AGGREGATOR_CONFIG["rounds"]
    assert len(list(agg_weights_dir.glob("*.pt"))) == AGGREGATOR_CONFIG["rounds"] + 1


def validate_participant_data(participants: dict, key: str, expected_value: str):
    for participant in participants:
        assert key in participant
        assert str(participant[key]) == str(expected_value)


async def validate_project_folder_empty(e2e_context: E2EContext, client: Client, timeout: int = 30):
    project_folder_dir = client.api_data_dir("fl_client") / "running" / PROJECT_NAME
    start_time = asyncio.get_event_loop().time()
    while project_folder_dir.exists():
        await asyncio.sleep(1)
        if start_time + timeout < asyncio.get_event_loop().time():
            raise TimeoutError(f"Project folder {project_folder_dir} not deleted in {timeout} seconds")

    assert not project_folder_dir.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("e2e_context", [deployment_config()], indirect=True, ids=["fl_model_training"])
async def test_e2e_fl_model_aggregator(e2e_context: E2EContext):
    logger.info(f"Starting E2E '{e2e_context.e2e_name}'")
    e2e_context.reset_test_dir()
    agg_client = e2e_context.clients[0]

    await e2e_context.start_all()
    await e2e_context.wait_for_api("fl_aggregator", agg_client, timeout=60)

    # copy launch config
    logger.info("Copying launch config")

    # sample launch config
    sample_dir = agg_client.api_dir / "fl_aggregator" / "samples"

    sample_launch_config = sample_dir / "launch_config"

    # global_model_weight.pt  model_arch.py

    launch_dir = agg_client.api_data_dir("fl_aggregator") / "launch"
    launch_dir.mkdir(parents=True, exist_ok=True)

    # copy model_arch.py and global_model_weight.pt
    shutil.copy(sample_launch_config / AGGREGATOR_CONFIG["model_arch"], launch_dir)
    shutil.copy(sample_launch_config / AGGREGATOR_CONFIG["model_weight"], launch_dir)

    # Copy Config
    fl_config = launch_dir / "fl_config.json"
    fl_config.write_text(json.dumps(AGGREGATOR_CONFIG))

    # Copy test dataset
    logger.info("Copying private test dataset for global model evaluation")
    sample_test_dataset = sample_dir / "test_data" / AGGREGATOR_CONFIG["test_dataset"]
    assert sample_test_dataset.exists()

    test_data_dir = agg_client.data_dir / "private" / "fl_aggregator"
    await e2e_context.wait_for_path(test_data_dir, timeout=240)
    assert test_data_dir.exists()

    # Add test dataset for global model evaluation
    shutil.copy(src=sample_test_dataset, dst=test_data_dir)
    logger.info(f"Test dataset copied to {test_data_dir} from {sample_test_dataset}")

    # Add private tests for fl clients
    logger.info("Copying private data to all FL clients")
    await asyncio.gather(*[copy_private_data(e2e_context, fl_client) for fl_client in e2e_context.clients[1:]])

    # Check FL client installed
    await asyncio.gather(*[check_fl_client_installed(e2e_context, fl_client) for fl_client in e2e_context.clients[1:]])

    # Approve data request for all FL Clients
    await asyncio.gather(*[approve_data_request(e2e_context, fl_client) for fl_client in e2e_context.clients[1:]])

    # Check dashboard metric data available
    logger.info("Checking for dashboard metric data")
    agg_public_dir = agg_client.public_dir / "fl" / PROJECT_NAME
    assert agg_public_dir.exists()

    assert (agg_public_dir / "participants.json").exists()
    assert (agg_public_dir / "accuracy_metrics.json").exists()

    # Check for training complete
    logger.info("Checking for training completion")
    await asyncio.gather(
        *[check_for_training_complete(e2e_context, fl_client) for fl_client in e2e_context.clients[1:]]
    )
    logger.info("All participants have completed training")

    # Validate results
    logger.info("Validating results")

    participant_file = agg_public_dir / "participants.json"
    participants = json.loads(participant_file.read_text())
    assert len(participants) == len(AGGREGATOR_CONFIG["participants"])

    logger.info("Validating participants metrics")
    participant_emails = [participant["Email"] for participant in participants]
    assert participant_emails == AGGREGATOR_CONFIG["participants"]

    # Validate participant metrics
    validate_participant_data(participants, "Fl Client Installed", True)
    validate_participant_data(participants, "Project Approved", True)
    validate_participant_data(participants, "Added Private Data", True)
    validate_participant_data(participants, "Round (current/total)", "3/3")

    accuracy_file = agg_public_dir / "accuracy_metrics.json"
    rounds_accuracy = json.loads(accuracy_file.read_text())

    logger.info("Validating accuracy metrics")
    assert len(rounds_accuracy) == AGGREGATOR_CONFIG["rounds"] + 1
    # Last round accuracy should be greater than 0.1
    assert rounds_accuracy[-1]["accuracy"] >= 0.1

    # Validate running folder is empty, post training
    await asyncio.gather(
        *[validate_project_folder_empty(e2e_context, fl_client) for fl_client in e2e_context.clients[1:]]
    )
