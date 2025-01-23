from datetime import datetime
import json
import os
from pymatgen.core import Structure
import requests
from typing import List, Optional


class CrystalStructure:
    formula: str
    structure: Structure
    energy: float
    submission_time: datetime
    def __init__(self, formula: str, structure: Structure, energy: float, submission_time: datetime):
        self.formula = formula
        self.structure = structure
        self.energy = energy
        self.submission_time = submission_time

    @staticmethod
    def request(params: dict) -> dict:
        access_key = os.environ.get("BOHRIUM_ACCESS_KEY")
        query_url = os.environ.get("OPENLAM_STRUCTURE_QUERY_URL", "http://openapi.dp.tech/openapi/v1/structures/query")
        headers = {
            "Content-type": "application/json",
        }
        params["accessKey"] = access_key
        rsp = requests.get(query_url, headers=headers, params=params)
        if rsp.status_code != 200:
            raise RuntimeError("Response code %s: %s" % (rsp.status_code, rsp.text))
        res = json.loads(rsp.text)
        if res["code"] != 0:
            raise RuntimeError("Query error code %s: %s" % (res["code"], res["error"]["msg"]))
        data = res["data"]
        return data

    @classmethod
    def query_by_page(
        cls,
        formula: Optional[str] = None,
        min_energy: Optional[float] = None,
        max_energy: Optional[float] = None,
        min_submission_time: Optional[datetime] = None,
        max_submission_time: Optional[datetime] = None,
        page: int = 1,
    ) -> dict:
        params = {
            "page": page,
        }
        if formula is not None:
            params["formula"] = formula
        if min_energy is not None:
            params["minEnergy"] = min_energy
        if max_energy is not None:
            params["maxEnergy"] = max_energy
        if min_submission_time is not None:
            params["minSubmissionTime"] = min_submission_time.isoformat()
        if max_submission_time is not None:
            params["maxSubmissionTime"] = max_submission_time.isoformat()

        data = cls.request(params)
        structures = []
        for item in data["items"]:
            structure = cls(formula=item["formula"],
                            structure=Structure.from_dict(json.loads(item["structure"])),
                            energy=item["energy"],
                            submission_time=datetime.fromisoformat(item["submissionTime"]))
            structures.append(structure)
        data["items"] = structures
        return data

    @classmethod
    def query(
        cls,
        formula: Optional[str] = None,
        min_energy: Optional[float] = None,
        max_energy: Optional[float] = None,
        min_submission_time: Optional[datetime] = None,
        max_submission_time: Optional[datetime] = None,
    ) -> List["CrystalStructure"]:
        page = 1
        structures = []
        while True:
            data = cls.query_by_page(formula=formula, min_energy=min_energy, max_energy=max_energy,
                                     min_submission_time=min_submission_time, max_submission_time=max_submission_time, page=page)
            structures += data["items"]
            if data["page"] * data["pageSize"] >= data["total"]:
                break
            page += 1
        return structures
