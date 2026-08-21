# ETL scripts

以下の処理を、公式データと取得日を記録しながら再実行可能なPythonコードとして実装する。

- `download_population.py`
- `download_shelters.py`
- `join_shelter_coordinates.py`
- `build_population_mesh.py`
- `build_tsunami_exposure.py`
- `build_walking_network.py`
- `calculate_evacuation_routes.py`
- `calculate_route_exposure.py`
- `calculate_capacity_pressure.py`
- `calculate_risk_score.py`
- `export_web_data.py`
- `validate_data.py`

現時点では、前回の詳細生成物が引き継がれていないため、スクリプトは再取得・再計算工程で実装する。
