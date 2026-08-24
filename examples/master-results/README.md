# Canonical master result-folder example

이 디렉터리는 개발 환경에서 `SIMDASH_IMPORT_ROOT`로 지정할 수 있는
마스터 결과 폴더의 최소 canonical 예제다. 폴더 계층은 DB의 업무 식별자와
같은 순서를 유지한다.

```text
master-results/
└── project-tv-001/
    └── request-drop-001/
        └── loadcase-drop-bottom-001/
            └── run-example-001/
                ├── manifest.json
                ├── results/summary.json
                ├── curves/open_cell_top_stress.csv
                └── media/
                    ├── open_cell_stress_contour.svg
                    └── chassis_sample.gltf
```

`manifest.json`의 `context`는 프로젝트·의뢰·하중 경우의 DB ID를 가리키고,
각 mapping의 `path`는 해당 run 폴더 내부의 상대 경로다.

| 파일 형식 | mapping kind / asset type | 저장 검증 |
| --- | --- | --- |
| `.json` | `typed_scalars` | FLOAT, INTEGER, TEXT, VERDICT scalar |
| `.csv` | `curve_csv` | 지정된 x/y 열의 유한 숫자 point |
| `.svg` | `media` / `IMAGE` | 안전한 SVG 최소 구조와 active-content 차단 |
| `.gltf` | `media` / `MODEL_3D` | `model/gltf+json`, JSON 구조 |

영상은 운영 폴더에서 `.mp4` 또는 `.webm`을 `media` / `VIDEO`로 추가할 수
있지만, 저장소 예제에는 큰 바이너리를 넣지 않는다. 미디어는 import 시
`asset_blobs`와 `asset_blob_chunks`에 저장되며 원본 파일 경로를 런타임에
신뢰하지 않는다.

검증 방법:

```bash
PYTHONPATH=backend ./.venv-wsl/bin/python -m pytest \
  backend/tests/test_master_result_folder_example.py -q
```
