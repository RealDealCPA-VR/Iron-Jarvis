import json, os

base = os.environ["RUNNER_TEMP"] + "/hay"
print(json.dumps({
    "kind": "grep", "base": base, "root": base,
    "opts": {"pattern": "need(le)+ h", "output_mode": "content", "before": 0, "after": 0,
             "case_insensitive": False, "glob": None, "file_type": None, "head_limit": 250,
             "offset": 0, "multiline": False, "line_numbers": True},
    "limits": {"deadline_s": 20, "max_files": 5000, "max_file_bytes": 2000000},
}))
