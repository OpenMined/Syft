import json
from typing import Dict, List
from os.path import dirname

def get_version_dict() -> Dict[str, List[str]]:
    print(dirname(dirname(__file__)))
    with open(dirname(dirname(__file__)) + "/version_matrix.json") as json_file:
        version_matrix = json.load(json_file)
    return version_matrix

def get_range_for_version(version_string: str) -> List[str]:
    print(f"{version_string=}")
    return get_version_dict()[version_string]