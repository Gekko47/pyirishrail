# Implementation Plan: Rewrite pyirishrail as a Home Assistant Bronze-tier Custom Integration

## Completion Checklist

- [ ] Step 1: Create `custom_components/irish_rail/const.py` with DOMAIN and scan interval.
- [ ] Step 2: Implement `custom_components/irish_rail/api.py` with async `IrishRailClient` and dataclasses.
- [ ] Step 3: Implement `custom_components/irish_rail/coordinator.py` using modern `entry.runtime_data` pattern.
- [ ] Step 4: Implement `custom_components/irish_rail/config_flow.py` with validation and duplicate checking.
- [ ] Step 5: Implement `custom_components/irish_rail/__init__.py` entry setup and unloading.
- [ ] Step 6: Implement `custom_components/irish_rail/entity.py` and `custom_components/irish_rail/sensor.py` with proper unique IDs and translation keys.
- [ ] Step 7: Implement `custom_components/irish_rail/strings.json`, `custom_components/irish_rail/translations/en.json`, and `custom_components/irish_rail/manifest.json`.
- [ ] Step 8: Implement `custom_components/irish_rail/quality_scale.yaml` detailing Bronze checklist.
- [ ] Step 9: Setup `pyproject.toml`, `hacs.json`, and `.github/workflows/ci.yml`.
- [ ] Step 10: Implement test suite under `tests/components/irish_rail/` (`test_api.py`, `test_config_flow.py`, `test_coordinator.py`, `test_init.py`, `conftest.py`).
- [ ] Step 11: Rewrite `README.md` with comprehensive installation, configuration, and removal guidelines.
- [ ] Step 12: Self-review against all Bronze rules and verify everything works.
