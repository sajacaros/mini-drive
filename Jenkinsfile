// Mini Drive CI/CD — 호스트 빌드 방식.
// Jenkins 는 트리거·오케스트레이션만 하고, 실제 이미지 빌드는 배포 호스트에서 수행한다.
// (Docker Hub 등 레지스트리 없음 → push/pull 없음.)
//
// 전제:
//   - Jenkins 자격증명: deploy-ssh (SSH Username with private key, 호스트 배포 계정)
//     ★ 레지스트리를 쓰지 않으므로 Jenkins 컨테이너에 docker CLI·소켓 마운트가 필요 없다.
//       Jenkins 는 SSH 만 하면 된다(SSH Agent 플러그인 + deploy-ssh 자격증명).
//   - 배포 호스트 /var/local/flex-drive 에 리포가 clone 돼 있고 deploy/.env 가 채워져 있음.
//   - 배포 계정이 docker 그룹에 속해 소켓 접근 가능(sudo usermod -aG docker deploy).
//   - 최초 스택 기동·관리자 셋업은 deploy/DEPLOY.md 0장 참고.

pipeline {
  agent any

  environment {
    DEPLOY_HOST = 'deploy@172.17.0.1'   // TODO: 호스트 배포계정@호스트IP
  }

  options {
    timestamps()
    disableConcurrentBuilds()
  }

  stages {
    stage('Deploy (host build)') {
      steps {
        sshagent(['deploy-ssh']) {
          // 호스트에서: origin/main 동기화 → compose build → up -d → 헬스체크(재시도).
          // 원격 스크립트를 작은따옴표로 감싸 로컬(Jenkins)에서 확장되지 않게 하고,
          // $변수·$(...) 는 전부 원격 셸에서 평가되게 한다(원격 블록엔 작은따옴표를 쓰지 않음).
          sh '''
            set -e
            ssh -o StrictHostKeyChecking=accept-new "$DEPLOY_HOST" '
              set -e
              cd /var/local/flex-drive
              echo "[1/5] git fetch + reset to origin/main"
              git fetch --all --prune
              git reset --hard origin/main
              git log --oneline -1

              cd deploy
              DC="docker compose -f docker-compose.deploy.yml"

              echo "[2/5] build (호스트에서 이미지 빌드)"
              $DC build

              echo "[3/5] up -d"
              $DC up -d

              echo "[4/5] prune dangling images"
              docker image prune -f

              echo "[5/5] health check"
              ok=0
              for i in $(seq 1 20); do
                if curl -fsS http://127.0.0.1:7755/health >/dev/null 2>&1; then ok=1; break; fi
                sleep 3
              done
              $DC ps
              if [ "$ok" = "1" ]; then
                echo "DEPLOY OK"
              else
                echo "HEALTH CHECK FAILED"
                $DC logs --tail 60 backend || true
                exit 1
              fi
            '
          '''
        }
      }
    }
  }

  post {
    success { echo "배포 완료 (호스트 빌드) — https://<host>/drive/" }
    failure { echo "배포 실패 — 로그 확인. 롤백: 호스트에서 git reset --hard <이전-sha> 후 재배포." }
  }
}
