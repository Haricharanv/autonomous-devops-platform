# Intelligent Autonomous DevOps Deployment Platform

**Project 40 — PBL DevOps**

A deployment platform that combines Docker, Kubernetes, Ansible, and a decision engine to automatically scale, restart, or rollback applications based on real-time monitoring.

## Team

| Name | USN |
|------|-----|
| Hari Charan V | 1RV25MC037 |
| M Srikanth | 1RV25MC053 |
| Anurag R Swamy | 1RV25MC016 |

## Tech Stack

- **App** — Python Flask REST API with Prometheus metrics
- **Container** — Docker (multi-stage build)
- **Orchestration** — Kubernetes (Minikube)
- **Automation** — Ansible playbooks
- **Monitoring** — Prometheus + Grafana
- **Decision Engine** — Python (rule-based + optional ML anomaly detection)
- **CI/CD** — GitHub Actions with Trivy image scanning

## Architecture

```
Developer → Git Push → GitHub Actions (test/build/scan)
                              ↓
                     Docker Image → Minikube Cluster
                              ↓
                     Ansible Playbooks (deploy/rollback/scale)
                              ↓
                     Prometheus (scrape metrics)
                              ↓
                     Decision Engine (analyze → act)
                              ↓
                     Auto-scale / Rollback / Restart
```

## Project Structure

```
PBL_DEVOPS/
├── app/                        # Flask application
│   ├── app.py                  # REST API (/health, /info, /load, /metrics, /ready, /live)
│   ├── requirements.txt
│   ├── wsgi.py                 # Gunicorn entry point
│   └── tests/                  # 33 pytest test cases
├── docker/
│   ├── Dockerfile              # Multi-stage build, non-root user
│   ├── .dockerignore
│   └── docker-compose.yml      # App + Prometheus + Grafana stack
├── kubernetes/
│   ├── namespace.yaml
│   ├── configmap.yaml
│   ├── deployment.yaml         # 2 replicas, resource limits, probes
│   ├── service.yaml            # NodePort
│   ├── hpa.yaml                # Auto-scale 2→10 pods at 70% CPU
│   ├── network-policy.yaml
│   └── service-monitor.yaml
├── ansible/
│   ├── ansible.cfg
│   ├── inventory/hosts.yml
│   └── playbooks/
│       ├── deploy.yml          # Full deployment
│       ├── rollback.yml        # Rollback to previous version
│       ├── scale.yml           # Scale replicas up/down
│       ├── restart.yml         # Rolling restart
│       └── status.yml          # Cluster status report
├── monitoring/
│   ├── prometheus/
│   │   ├── prometheus.yml      # Scrape configs
│   │   └── alert_rules.yml     # 8 alert rules
│   └── grafana/
│       ├── dashboards/         # 8-panel dashboard
│       └── datasources/
├── ai-engine/
│   ├── config.yaml             # Thresholds and settings
│   ├── metrics_collector.py    # Prometheus PromQL queries
│   ├── decision_engine.py      # Rule-based decision logic
│   ├── action_executor.py      # Runs kubectl/ansible commands
│   ├── notifier.py             # Slack/email alerts
│   ├── anomaly_detector.py     # Isolation Forest anomaly detection
│   ├── main.py                 # CLI entry point
│   └── requirements.txt
├── .github/workflows/
│   └── ci-cd.yml               # Test → Build → Scan → Deploy
├── scripts/
│   ├── setup.sh                # Install all prerequisites
│   ├── demo.sh                 # Full demo walkthrough
│   ├── load_test.py            # Stress testing tool
│   └── cleanup.sh              # Teardown everything
├── README.md
└── .gitignore
```

## Decision Engine Rules

| Condition | Threshold | Action |
|-----------|-----------|--------|
| High CPU | > 80% for 2 min | Scale up by 2 replicas |
| Low CPU | < 20% for 5 min | Scale down by 1 replica |
| High Memory | > 85% for 2 min | Scale up by 2 replicas |
| High Error Rate | > 10% of requests are 5xx | Rollback to previous version |
| Pod CrashLoop | > 3 restarts in 5 min | Restart pod, then rollback |
| High Latency | p95 > 1 second | Scale up |

## Getting Started

### Prerequisites

- Docker
- Minikube + kubectl
- Ansible
- Python 3.10+
- Helm (for Prometheus/Grafana)

### Setup

```bash
# 1. Clone the repo
git clone https://github.com/<your-username>/PBL_DEVOPS.git
cd PBL_DEVOPS

# 2. Run setup script (installs tools, starts Minikube)
chmod +x scripts/setup.sh
./scripts/setup.sh

# 3. Build and load Docker image
docker build -f docker/Dockerfile -t devops-demo-app:latest ./app
minikube image load devops-demo-app:latest

# 4. Deploy with Ansible
cd ansible
ansible-playbook playbooks/deploy.yml

# 5. Check status
ansible-playbook playbooks/status.yml

# 6. Run load test
SERVICE_URL=$(minikube service devops-demo-app -n devops-platform --url)
python3 scripts/load_test.py --url $SERVICE_URL --duration 30 --concurrency 10

# 7. Start the decision engine
cd ai-engine
pip install -r requirements.txt
python main.py --config config.yaml --once --dry-run
```

### Ansible Playbook Usage

```bash
# Deploy
ansible-playbook playbooks/deploy.yml

# Scale to 5 replicas
ansible-playbook playbooks/scale.yml -e "replicas=5"

# Rollback
ansible-playbook playbooks/rollback.yml

# Restart pods
ansible-playbook playbooks/restart.yml

# Check status
ansible-playbook playbooks/status.yml
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Welcome page with endpoint list |
| GET | `/health` | Health check |
| GET | `/info` | App info + team members |
| GET | `/ready` | Readiness probe |
| GET | `/live` | Liveness probe |
| POST | `/load` | CPU stress test (`{"duration": N, "intensity": N}`) |
| GET | `/metrics` | Prometheus metrics |

## Troubleshooting

**Docker permission denied:**
```bash
sudo usermod -aG docker $USER && newgrp docker
```

**HPA shows `<unknown>`:** Wait 60s for metrics-server to collect data, or:
```bash
minikube addons enable metrics-server
```

**Port already in use:**
```bash
pkill -f "kubectl port-forward"
```

## License

MIT
