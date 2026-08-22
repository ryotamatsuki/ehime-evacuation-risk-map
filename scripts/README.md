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

現時点では、取得・結合・人口メッシュ・津波曝露・歩行ネットワーク抽出まで実装済みです。経路・収容負荷・スコア・総合QAは未実装であり、推測値は出力しません。`build_walking_network.py --summarize-only` は、既存の市町別ネットワークQAを再集計します。
