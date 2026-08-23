# Analysis / ETL scripts

このディレクトリでは、公式データ取得から Analysis Core v4、STEP 5 robustness、STEP 7 policy simulation QA までを再実行可能なPythonコードとして管理します。

## 現行の正本パイプライン

1. 取得・前処理
   - `download_population.py`
   - `download_shelters.py`
   - `join_shelter_coordinates.py`
   - `build_population_mesh.py`
   - `build_tsunami_exposure.py`
2. STEP 1 Routing Foundation
   - `build_walking_network.py`
   - `routing_foundation_qa.py`
   - `select_mesh_origins.py`
   - `enforce_step1_gate.py`
3. STEP 2 Mesh → Shelter Routing
   - `calculate_evacuation_routes_v2.py`
   - `aggregate_step2_routes.py`
4. STEP 3 Route Tsunami Exposure
   - `calculate_route_exposure_step3.py`
5. STEP 4 Demand / Capacity / Risk
   - `calculate_step4_demand_capacity_risk.py`
6. corrected public export
   - `export_corrected_public_data.py`
7. STEP 5 robustness / final analytical QA
   - `analyze_step5_sensitivity.py`
8. STEP 7 policy simulation QA
   - `validate_policy_simulation.py`
9. committed source/intermediate data QA
   - `validate_source_data.py`
10. cross-stage production regression
   - `verify_production_regression.py`

GraphML、公式津波ラスタ等の大容量中間データはGitHub Pagesの公開資産へ複製しません。公開サイトのAnalysis Coreデータは `routing-step2.yml` の同一production runで生成した `corrected-final-export` を正本とします。

## 重要な分析契約

- 対象は津波浸水メッシュ1,090件です。
- STEP 4の需要代理値は `人口 × 津波浸水面積割合` です。
- 避難場所の収容人数が未公表の場合、容量・収容負荷は不明のまま保持し、0や推測値で補完しません。
- 5要素スコアは5要素がすべて揃う `score_status=complete` のみ数値を公開します。欠損要素の重み再配分やブラウザ側の再計算は行いません。
- STEP 7は既知の公表容量だけを仮想増強するcounterfactualです。経路再計算、超過需要の再配分、canonical STEP 4 scoreの書換えは行いません。

旧5,821行系のrouting/riskスクリプトはSTEP 7.5で削除しました。履歴確認が必要な場合はGit履歴を参照し、現行分析へ再接続しないでください。
