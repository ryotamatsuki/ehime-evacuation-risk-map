# 愛媛県「南海トラフ・本当に逃げられるかマップ」

愛媛県沿岸14市町を対象に、人口、津波浸水曝露、避難場所までの徒歩距離、避難経路の津波浸水曝露、避難場所の公表収容人数を重ね合わせ、500mメッシュ単位で避難困難度と政策上の優先候補を探索するWebGISです。

## 現在の分析基盤

Analysis Core v4 corrected の STEP 1〜4 是正フェーズは完了しています。2026-08-23時点の基準は次のとおりです。

- 津波分析対象：1,090メッシュ（`tsunami_inundation_ratio > 0`）
- 完全な避難経路：1,062メッシュ
- 経路未成立：28メッシュ（26 `no_network_path`、2 `network_coverage_gap`）
- 市町境を越える避難経路：13メッシュ
- 5要素スコア完全：813メッシュ
- 収容人数欠損のためcore scoreのみ：128メッシュ
- core data incomplete：121メッシュ
- 面積按分需要で収容負荷100%超：35施設
- full-mesh感度シナリオで収容負荷100%超：72施設

補正済みproduction run `32598837155` は main SHA `42736642f668ef35616ef26297941dabce73f002` を入力として STEP 2→3→4→FINAL B→FINAL C を完走し、GitHub Pages deployまで成功しています。

STEP 5 Sensitivity / Robustness / Final QA も完了しています。12の重みシナリオで813件のcomplete scoreだけを比較した結果、全シナリオで上位10%を維持したメッシュは69件、全シナリオでTop 50を維持したメッシュは42件でした。等重みシナリオとbaselineの順位相関は0.9894で、上位層の政策優先順位はテストした重み範囲では概ね頑健です。

## 分析方法

### STEP 1 — Routing Foundation & Spatial QA

N03市町村MultiPolygonを基礎にEPSG:32653で3kmバッファを作成し、OSM歩行ネットワークの接続性、メッシュ起点、避難場所接続、市町境・離島ケースを検証しました。500mを超える架空の接続は作成しません。

### STEP 2 — Cross-border Mesh → Shelter Routing

同一市町内だけに避難先を制限せず、分析AOI内で到達可能な津波対応避難場所を候補にして歩行ネットワーク距離で選択します。経路が成立しないメッシュは削除せず、明示的な失敗状態として保持します。

### STEP 3 — Route Tsunami Exposure

補正済み経路上をサンプリングして津波浸水曝露を計算します。経路曝露は道路の被災確率ではなく、モデル化した歩行経路と津波ラスターの空間的重なりです。ラスター不明区間は不明のまま保持します。

### STEP 4 — Evacuation Demand / Capacity Pressure / Exploratory Risk

主要な避難需要代理値は、

`total_population × tsunami_inundation_ratio`

です。500mメッシュ内で人口が一様に分布すると仮定した面積按分proxyであり、実避難者数の予測ではありません。感度確認用にfull-mesh人口シナリオも保持します。

収容負荷は、選択された避難場所ごとに需要を先に集約し、その後で公表収容人数で除します。収容人数が未公表の場合はnullのままとし、0や推測値で補完しません。容量超過分の自動再配分も行いません。

### 探索的5要素スコア

PoCの基準重みは次のとおりです。

- 津波浸水曝露：25
- 65歳以上人口割合：20
- 徒歩アクセス：25
- 経路津波曝露：15
- 避難場所収容負荷：15

これは公式の政策基準ではありません。5要素がすべて揃う `score_status=complete` の場合だけ `evacuation_difficulty_score` を表示します。欠損した要素を除外して残りの重みを再配分する処理は行いません。

## STEP 5 — Sensitivity / Robustness

基準重み、各要素の重みを1つずつ±20%変更した10ケース、等重みの計12シナリオを比較します。順位相関、Top 10%重複、Top 50重複、各メッシュの順位レンジ、全シナリオ共通の上位メッシュを算出します。欠損・経路未成立277メッシュは低リスク扱いせず、感度順位から明示的に除外します。

詳細は `docs/step5-sensitivity-methodology.md` と `docs/step5-sensitivity-results.md` を参照してください。

## データと再現性

- `scripts/`：データ取得・加工・分析・検証コード
- `public/data/`：Web表示用データ。ただしproductionの補正済み1,090メッシュデータはCIで同一runから生成してPages artifactへ格納
- `data/qa/`：GIS・結合・分析QA
- `docs/`：各STEPの方法・結果・制約
- `.github/workflows/`：STEP別release gate、production export、Pages deploy、ブラウザ回帰テスト

通常のフロントエンドbuildは、Analysis Core v4のUI契約回帰テストを先に実行します。

```bash
npm install
npm run build
```

GraphML歩行ネットワーク、OSMキャッシュ、津波原典タイルはETL中間データであり、公開リポジトリには格納しません。

## 公開URL

https://ryotamatsuki.github.io/ehime-evacuation-risk-map/

## ブラウザQA

Chromium系のproduction DOM確認に加え、STEP 6ではGitHub Actions上のPlaywright WebKitを使用してdesktopおよびiPhone相当viewportのproduction smoke testを行います。WebKitテストはSafari互換性の強い自動回帰チェックですが、実機のSafari / iPhone Safariそのものではありません。native Safari実機確認は別途manual QAとして扱います。

## 重要な制約

- 分析は公開データに基づくPoCで、災害時の安全や実際の避難行動を保証しません。
- 面積按分需要はproxy、full-mesh人口は感度シナリオです。
- 経路津波曝露は道路通行不能確率ではありません。
- 収容負荷はcapacity-constrained assignmentではなく、選択避難先の診断値です。
- 欠損値、秘匿値、未照合施設を推測値で補完しません。
- 5要素スコアと重みは探索的で、公式の政策基準ではありません。
- 2026年愛媛県地震被害想定GISの大容量ZIPは参照元であり、リポジトリには格納しません。

## 免責

本サイトは公開データを用いた政策分析・可視化PoCです。災害時および個別地点の防災判断には、愛媛県・各市町等が公表する最新の防災情報・ハザードマップを確認してください。
