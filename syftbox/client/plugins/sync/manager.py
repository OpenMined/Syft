import time
from threading import Thread
from typing import Optional

from loguru import logger

from syftbox.client.base import SyftBoxContextInterface
from syftbox.client.exceptions import SyftAuthenticationError
from syftbox.client.plugins.sync.consumer import SyncConsumer
from syftbox.client.plugins.sync.exceptions import FatalSyncError, SyncEnvironmentError
from syftbox.client.plugins.sync.local_state import LocalState
from syftbox.client.plugins.sync.producer import SyncProducer
from syftbox.client.plugins.sync.queue import SyncQueue, SyncQueueItem
from syftbox.client.plugins.sync.types import FileChangeInfo


class SyncManager:
    def __init__(self, context: SyftBoxContextInterface, health_check_interval: int = 300):
        self.context = context
        self.queue = SyncQueue()
        self.local_state = LocalState.for_context(context)
        self.producer = SyncProducer(context=self.context, queue=self.queue, local_state=self.local_state)
        self.consumer = SyncConsumer(context=self.context, queue=self.queue, local_state=self.local_state)

        self.sync_interval = 0.0001  # seconds
        self.thread: Optional[Thread] = None
        self.is_stop_requested = False
        self.sync_run_once = False
        self.last_health_check = 0.0
        self.health_check_interval = float(health_check_interval)

        self.setup()

    def setup(self) -> None:
        try:
            self.local_state.load()
        except Exception as e:
            raise SyncEnvironmentError(f"Failed to load previous sync state: {e}") from e

    def is_alive(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def stop(self, blocking: bool = False) -> None:
        self.is_stop_requested = True
        if blocking and self.thread is not None:
            self.thread.join()

    def start(self) -> None:
        def _start(manager: SyncManager) -> None:
            while not manager.is_stop_requested:
                try:
                    if manager._should_perform_health_check():
                        manager.check_server_status()
                    manager.run_single_thread()
                    time.sleep(manager.sync_interval)
                except FatalSyncError as e:
                    logger.error(f"Syncing encountered a fatal error. {e}")
                    break
                except Exception as e:
                    logger.error(f"Syncing encountered an error: {e}. Retrying in {manager.sync_interval} seconds.")

        self.is_stop_requested = False
        t = Thread(target=_start, args=(self,), daemon=True)
        t.start()
        logger.info(f"Sync started, syncing every {self.sync_interval} seconds")
        self.thread = t

    def enqueue(self, change: FileChangeInfo) -> None:
        self.queue.put(SyncQueueItem(priority=change.get_priority(), data=change))

    def _should_perform_health_check(self) -> bool:
        return time.time() - self.last_health_check > self.health_check_interval

    def check_server_status(self) -> None:
        """
        check if the server is still available for syncing,
        if the user cannot authenticate, the sync will stop.

        Raises:
            FatalSyncError: If the server is not available.
        """
        try:
            _ = self.context.client.auth.whoami()
            logger.debug("Health check succeeded, server is available.")
            self.last_health_check = time.time()
        except SyftAuthenticationError as e:
            # Auth errors will never recover, sync should be stopped
            raise FatalSyncError(f"Health check failed, {e}") from e
        except Exception as e:
            logger.error(f"Health check failed: {e}. Retrying in {self.health_check_interval} seconds.")

    def run_single_thread(self) -> None:
        datasite_states = self.producer.get_datasite_states()
        logger.debug(f"Syncing {len(datasite_states)} datasites")

        if not self.sync_run_once:
            # Download all missing files at the start
            self.consumer.download_all_missing(datasite_states=datasite_states)

        for datasite_state in datasite_states:
            self.producer.enqueue_datasite_changes(datasite_state)

        # TODO stop consumer if self.is_stop_requested
        self.consumer.consume_all()

        self.sync_run_once = True
