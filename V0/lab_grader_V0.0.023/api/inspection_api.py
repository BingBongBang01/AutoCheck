"""InspectionApiMixin — 현재 프로젝트의 점검 프로파일(target_state/stages) 조회."""


class InspectionApiMixin:
    def get_inspection_profile(self):
        try:
            paths = self._paths()
        except RuntimeError:
            return None
        import yaml
        with open(paths["stages"], encoding="utf-8") as f:
            stages_cfg = yaml.safe_load(f)["stages"]
        with open(paths["target_state"], encoding="utf-8") as f:
            target_state = yaml.safe_load(f) or {}

        result = []
        for stage in stages_cfg:
            checks = target_state.get(stage["id"], {}).get("checks", [])
            result.append({
                "id": stage["id"], "label": stage["label"],
                "depends_on": stage.get("depends_on", []),
                "commands": stage.get("commands", []),
                "check_count": len(checks),
            })
        return result
