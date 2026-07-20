// Mini Drive CI/CD — Docker Hub 빌드·푸시 후 같은 호스트에 SSH 배포.
//
// 전제:
//   - Jenkins 는 컨테이너로 실행 중이며 docker CLI + 호스트 docker.sock 접근 가능
//     (docker run ... -v /var/run/docker.sock:/var/run/docker.sock, docker CLI 설치).
//   - Jenkins 자격증명:
//       dockerhub    : Username/Password (Docker Hub 계정, push 권한)
//       deploy-ssh   : SSH Username with private key (호스트 배포 계정)
//   - 호스트 /opt/flex-drive 에 리포가 git clone 돼 있고 deploy/.env 가 채워져 있음.
//     (최초 TLS 발급은 deploy/init-letsencrypt.sh 로 1회 수행.)
//
// 파라미터로 덮어쓰지 않는 한 아래 기본값 사용.

pipeline {
  agent any

  environment {
    DOCKERHUB_USER = 'your-dockerhub-username'         // TODO: 실제 계정으로
    IMAGE_BACKEND  = "${DOCKERHUB_USER}/flex-drive-backend"
    IMAGE_FRONTEND = "${DOCKERHUB_USER}/flex-drive-frontend"
    DEPLOY_HOST    = 'deploy@172.17.0.1'               // TODO: 호스트 배포계정@호스트IP
    DEPLOY_DIR     = '/opt/flex-drive'
  }

  options {
    timestamps()
    disableConcurrentBuilds()
  }

  stages {
    stage('Checkout') {
      steps {
        checkout scm
        script {
          // 7자리 짧은 커밋 SHA 를 이미지 태그로
          env.TAG = sh(script: 'git rev-parse --short=7 HEAD', returnStdout: true).trim()
        }
      }
    }

    stage('Build & Push') {
      steps {
        withCredentials([usernamePassword(
            credentialsId: 'dockerhub',
            usernameVariable: 'DH_USER',
            passwordVariable: 'DH_PASS')]) {
          sh '''
            set -e
            echo "$DH_PASS" | docker login -u "$DH_USER" --password-stdin

            docker build -t $IMAGE_BACKEND:$TAG -t $IMAGE_BACKEND:latest ./backend
            docker build -t $IMAGE_FRONTEND:$TAG -t $IMAGE_FRONTEND:latest ./frontend

            docker push $IMAGE_BACKEND:$TAG
            docker push $IMAGE_BACKEND:latest
            docker push $IMAGE_FRONTEND:$TAG
            docker push $IMAGE_FRONTEND:latest

            docker logout
          '''
        }
      }
    }

    stage('Deploy') {
      steps {
        sshagent(['deploy-ssh']) {
          sh '''
            set -e
            ssh -o StrictHostKeyChecking=accept-new "$DEPLOY_HOST" "
              set -e
              cd '$DEPLOY_DIR'
              git pull --ff-only
              cd deploy
              export IMAGE_TAG='$TAG'
              docker compose -f docker-compose.deploy.yml pull backend frontend
              docker compose -f docker-compose.deploy.yml up -d
              docker image prune -f
            "
          '''
        }
      }
    }

    stage('Smoke test') {
      steps {
        sshagent(['deploy-ssh']) {
          sh '''
            ssh -o StrictHostKeyChecking=accept-new "$DEPLOY_HOST" \
              "curl -fsS -o /dev/null -w 'health=%{http_code}\\n' http://127.0.0.1:8080/health"
          '''
        }
      }
    }
  }

  post {
    success { echo "배포 완료: $IMAGE_BACKEND:$TAG" }
    failure { echo "배포 실패 — 로그 확인. 롤백: 이전 태그로 IMAGE_TAG 지정 후 up -d" }
  }
}
