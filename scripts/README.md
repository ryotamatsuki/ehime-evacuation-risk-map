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

実行順の目安は、取得 → 結合 → 人口メッシュ／津波曝露 → 歩行ネットワーク → 経路 → 経路曝露 → 容量負荷 → スコア → Web出力 → QAです。GraphMLと津波タイルはローカル中間データであり、`public/data`へ複製しません。

`calculate_evacuation_routes.py` は500mメッシュ代表点をOSM pedestrian networkへスナップし、同一市町内の津波対応施設を複数候補としてネットワーク距離最短の施設へ仮割当します。`0.5m/s` は移動制約シナリオであり、65歳以上全員の歩行速度を意味しません。

`calculate_route_exposure.py` はルートポリラインを25m間隔で公式津波ラスタへサンプルし、`route_inundation_ratio` と `route_inundation_distance_m` を算定します。これは避難経路の津波浸水曝露度であり、道路寸断確率ではありません。

`calculate_capacity_pressure.py` は収容人数欠損を0にせず、津波対応施設の同一共通ID重複は対応レコードだけを合算します。`calculate_risk_score.py` は5要素を0–100へ正規化し、欠損要素がある場合は利用可能な重みで再正規化します。重みはPoC用探索的重みです。

`validate_data.py --strict` の現行結果は、失敗0・注意4です。注意は未照合施設28、施設属性の重複共通ID4、津波対応施設の容量欠損282、ネットワーク経路なし57です。
