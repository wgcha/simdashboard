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
                ├── .simdashboard-ready.json
                ├── results/summary.json
                ├── curves/open_cell_top_stress.csv
                └── media/
                    ├── open_cell_stress_contour.svg
                    └── chassis_sample.gltf
```

`manifest.json`의 `context`는 프로젝트·의뢰·하중 경우의 DB ID를 가리키고,
각 mapping의 `path`는 해당 run 폴더 내부의 상대 경로다. 예제는 안정적인
`source_run_id`와 `conflict_policy: SKIP`을 사용한다.

`.simdashboard-ready.json`은 marker v1의 checked-in 검증 예제다. marker는
`manifest.json`과 mapping bytes에서 계산한 checksum/fingerprint를 선언하지만,
fingerprint 항목 자체에는 포함되지 않는다. 운영 producer는 source bundle을 직접
`SIMDASH_IMPORT_ROOT`에 쓰지 말고 전용 producer 계정에서 아래 CLI로 publish한다.
앱 systemd service 계정은 import root를 read-only로만 연다.

```bash
PYTHONPATH=backend ./.venv-wsl/bin/python backend/scripts/publish_result_bundle.py \
  --source-bundle /srv/solver-output/run-example-001 \
  --import-root /var/lib/simdashboard/import \
  --publication-id run-example-001
```

`--source-bundle`은 producer가 소유한 완료된 source이고, `--import-root` 아래의
final path는 `{project_id}/{request_id}/{load_case_id}/{publication_id}`이다. 이미
존재하는 final path를 교체하지 않으며 오류로 종료한다.

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

AP-1 WSL integrated verification은 이 strict marker example을 포함해
`122 passed in 77.07s`였고, full backend는 `533 passed, 5 skipped in 366.02s`였다. 실제
Rocky host deploy와 NFS/SMB mount capability는 별도 운영 release gate다.
