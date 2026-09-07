# 사내 인증서

사내에서 제공받은 공개 CA 인증서 `DigitalCity.crt`를 이 폴더에 놓으면 Windows 설치기가 찾는다. 인증서가 이미 Windows 신뢰 저장소에 등록되어 있으면 별도 파일 없이 사용할 수 있다.

인증서 파일은 Git에 포함하지 않는다. 인증서의 개인 키, 계정 비밀번호 또는 토큰은 이 폴더에 넣지 않는다.

설치와 실행은 저장소 루트의 `deploy.bat`, `start.bat`를 사용한다. 세부 설정은 [Windows 배치 안내](../../../docs/windows-one-click-deployment.md)를 따른다.
