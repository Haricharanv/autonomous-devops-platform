#!/usr/bin/env bash
# ==============================================================================
# Intelligent Autonomous DevOps Deployment Platform - Environment Setup Script
# Description: Automated verification, installation, and cluster bootstrap for
#              Docker, Minikube, Kubectl, Ansible, Python3, Pip, Helm, Trivy,
#              Prometheus/Grafana monitoring, and Kubernetes namespaces.
# ==============================================================================

set -eo pipefail

# ------------------------------------------------------------------------------
# Color Constants & UI Formatting
# ------------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# ------------------------------------------------------------------------------
# Logging Helper Functions
# ------------------------------------------------------------------------------
log_title() {
    echo -e "\n${BOLD}${PURPLE}==============================================================================${NC}"
    echo -e "${BOLD}${CYAN}  $1${NC}"
    echo -e "${BOLD}${PURPLE}==============================================================================${NC}\n"
}

log_step() {
    echo -e "${BOLD}${BLUE}==>${NC} ${BOLD}${WHITE}$1${NC}"
}

log_info() {
    echo -e "  ${BLUE}ℹ [INFO]${NC} $1"
}

log_success() {
    echo -e "  ${GREEN}✓ [SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "  ${YELLOW}⚠ [WARN]${NC} $1"
}

log_error() {
    echo -e "  ${RED}✗ [ERROR]${NC} $1"
}

# ------------------------------------------------------------------------------
# Script Flags & Defaults
# ------------------------------------------------------------------------------
AUTO_INSTALL=false
SKIP_CLUSTER=false
SKIP_MONITORING=false
DRY_RUN=false

show_help() {
    cat << EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  -y, --auto-install     Automatically attempt package installation if missing
  --skip-cluster         Skip starting Minikube cluster and addons
  --skip-monitoring      Skip deploying Prometheus & Grafana Helm charts
  --dry-run              Check prerequisites without making any system changes
  -h, --help             Display this help message and exit

Description:
  Sets up the full local DevOps environment for the Autonomous Deployment Platform.
  Verifies Docker, Minikube, Kubectl, Ansible, Python3, Pip, Helm, and Trivy.
  Bootstraps a 2-CPU / 4096MB Minikube cluster with metrics-server, dashboard,
  and ingress, and configures Prometheus and Grafana for observability.
EOF
    exit 0
}

# Parse command line options
while [[ $# -gt 0 ]]; do
    case "$1" in
        -y|--auto-install)
            AUTO_INSTALL=true
            shift
            ;;
        --skip-cluster)
            SKIP_CLUSTER=true
            shift
            ;;
        --skip-monitoring)
            SKIP_MONITORING=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        -h|--help)
            show_help
            ;;
        *)
            log_error "Unknown option: $1"
            echo "Use --help for usage information."
            exit 1
            ;;
    esac
done

# ------------------------------------------------------------------------------
# Banner
# ------------------------------------------------------------------------------
echo -e "${BOLD}${CYAN}"
cat << "EOF"
  ___            ___            ___      _       _           _   
 |   \ _____ __ / _ \ _ __  ___| _ \__ _| |_ ___| |_  ___ __| |  
 | |) / -_) V // (_) | '_ \(_-<  _/ _` |  _/ _| ' \/ -_) _` |  
 |___/\___|\_/  \___/| .__/__/_/_| \__,_|\__\__|_||_\___|__,_|  
                     |_|                                         
  Intelligent Autonomous DevOps Deployment Platform Setup
EOF
echo -e "${NC}"

# ------------------------------------------------------------------------------
# Step 1: Operating System & Architecture Detection
# ------------------------------------------------------------------------------
log_title "STEP 1: Operating System & Architecture Detection"

OS_NAME="$(uname -s)"
ARCH="$(uname -m)"

case "${ARCH}" in
    x86_64)
        K8S_ARCH="amd64"
        TRIVY_ARCH="64bit"
        ;;
    aarch64|arm64)
        K8S_ARCH="arm64"
        TRIVY_ARCH="ARM64"
        ;;
    *)
        log_warn "Unsupported architecture '${ARCH}'. Defaulting to amd64."
        K8S_ARCH="amd64"
        TRIVY_ARCH="64bit"
        ;;
esac

DISTRO="unknown"
DISTRO_NAME="Unknown Linux"
PACKAGE_MANAGER="unknown"

if [[ "${OS_NAME}" == "Linux" ]]; then
    if [[ -f /etc/os-release ]]; then
        # shellcheck disable=SC1091
        source /etc/os-release
        DISTRO="${ID:-unknown}"
        DISTRO_NAME="${PRETTY_NAME:-Linux}"
    fi

    if command -v apt-get >/dev/null 2>&1; then
        PACKAGE_MANAGER="apt"
    elif command -v dnf >/dev/null 2>&1; then
        PACKAGE_MANAGER="dnf"
    elif command -v yum >/dev/null 2>&1; then
        PACKAGE_MANAGER="yum"
    elif command -v pacman >/dev/null 2>&1; then
        PACKAGE_MANAGER="pacman"
    fi
elif [[ "${OS_NAME}" == "Darwin" ]]; then
    DISTRO="macos"
    DISTRO_NAME="macOS $(sw_vers -productVersion 2>/dev/null || echo '')"
    if command -v brew >/dev/null 2>&1; then
        PACKAGE_MANAGER="brew"
    fi
fi

log_info "Operating System: ${DISTRO_NAME} (${OS_NAME})"
log_info "CPU Architecture: ${ARCH} (Kubernetes target: ${K8S_ARCH})"
log_info "Package Manager:  ${PACKAGE_MANAGER}"

HAS_SUDO=false
if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
    HAS_SUDO=true
elif [[ $EUID -eq 0 ]]; then
    HAS_SUDO=true
fi

# ------------------------------------------------------------------------------
# Step 2: Tool Verification & Installation Guidance
# ------------------------------------------------------------------------------
log_title "STEP 2: Prerequisites Verification & Installation"

declare -A TOOLS_STATUS
declare -A TOOLS_VERSION

# Helper: Prompt or run auto-install
prompt_or_install() {
    local tool_name="$1"
    local install_cmd="$2"

    if [[ "${DRY_RUN}" == "true" ]]; then
        log_info "Dry-run mode: skipping installation of ${tool_name}"
        return 0
    fi

    if [[ "${AUTO_INSTALL}" == "true" ]]; then
        log_step "Auto-installing ${tool_name}..."
        eval "${install_cmd}"
    else
        echo -e "\n  ${YELLOW}To install ${tool_name} on Ubuntu/Debian, execute:${NC}"
        echo -e "  ${BOLD}${WHITE}${install_cmd}${NC}\n"
    fi
}

# --- Tool 1: Docker ---
log_step "Checking Docker..."
if command -v docker >/dev/null 2>&1; then
    DOCKER_VER=$(docker --version | head -n1)
    TOOLS_STATUS["docker"]="INSTALLED"
    TOOLS_VERSION["docker"]="${DOCKER_VER}"
    log_success "Docker is installed: ${DOCKER_VER}"

    # Check if docker daemon is running
    if docker info >/dev/null 2>&1; then
        log_success "Docker daemon is running and accessible."
    else
        log_warn "Docker daemon is not responding or current user lacks socket permissions."
        log_info "Run: 'sudo systemctl start docker' and 'sudo usermod -aG docker \$USER'"
    fi
else
    TOOLS_STATUS["docker"]="MISSING"
    TOOLS_VERSION["docker"]="Not Found"
    log_error "Docker is NOT installed!"
    INSTALL_DOCKER="sudo apt-get update && sudo apt-get install -y ca-certificates curl gnupg && \
sudo install -m 0755 -d /etc/apt/keyrings && \
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg && \
sudo chmod a+r /etc/apt/keyrings/docker.gpg && \
echo \"deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo \"$VERSION_CODENAME\") stable\" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null && \
sudo apt-get update && sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin && \
sudo usermod -aG docker \$USER"
    prompt_or_install "Docker" "${INSTALL_DOCKER}"
fi

# --- Tool 2: Minikube ---
log_step "Checking Minikube..."
if command -v minikube >/dev/null 2>&1; then
    MINIKUBE_VER=$(minikube version --short 2>/dev/null || minikube version | head -n1)
    TOOLS_STATUS["minikube"]="INSTALLED"
    TOOLS_VERSION["minikube"]="${MINIKUBE_VER}"
    log_success "Minikube is installed: ${MINIKUBE_VER}"
else
    TOOLS_STATUS["minikube"]="MISSING"
    TOOLS_VERSION["minikube"]="Not Found"
    log_error "Minikube is NOT installed!"
    INSTALL_MINIKUBE="curl -LO https://storage.googleapis.com/minikube/releases/latest/minikube-linux-${K8S_ARCH} && \
sudo install minikube-linux-${K8S_ARCH} /usr/local/bin/minikube && \
rm -f minikube-linux-${K8S_ARCH}"
    prompt_or_install "Minikube" "${INSTALL_MINIKUBE}"
fi

# --- Tool 3: Kubectl ---
log_step "Checking Kubectl..."
if command -v kubectl >/dev/null 2>&1; then
    KUBECTL_VER=$(kubectl version --client --output=yaml 2>/dev/null | grep 'gitVersion:' | awk '{print $2}' || kubectl version --client 2>&1 | head -n1)
    TOOLS_STATUS["kubectl"]="INSTALLED"
    TOOLS_VERSION["kubectl"]="${KUBECTL_VER}"
    log_success "Kubectl is installed: ${KUBECTL_VER}"
else
    TOOLS_STATUS["kubectl"]="MISSING"
    TOOLS_VERSION["kubectl"]="Not Found"
    log_error "Kubectl is NOT installed!"
    INSTALL_KUBECTL="K8S_LATEST=\$(curl -L -s https://dl.k8s.io/release/stable.txt) && \
curl -LO \"https://dl.k8s.io/release/\${K8S_LATEST}/bin/linux/${K8S_ARCH}/kubectl\" && \
sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl && \
rm -f kubectl"
    prompt_or_install "Kubectl" "${INSTALL_KUBECTL}"
fi

# --- Tool 4: Ansible ---
log_step "Checking Ansible..."
if command -v ansible >/dev/null 2>&1; then
    ANSIBLE_VER=$(ansible --version | head -n1 | awk '{print $1, $2}')
    TOOLS_STATUS["ansible"]="INSTALLED"
    TOOLS_VERSION["ansible"]="${ANSIBLE_VER}"
    log_success "Ansible is installed: ${ANSIBLE_VER}"
else
    TOOLS_STATUS["ansible"]="MISSING"
    TOOLS_VERSION["ansible"]="Not Found"
    log_error "Ansible is NOT installed!"
    INSTALL_ANSIBLE="sudo apt-get update && sudo apt-get install -y ansible || pip3 install --user ansible"
    prompt_or_install "Ansible" "${INSTALL_ANSIBLE}"
fi

# --- Tool 5: Python 3 ---
log_step "Checking Python 3..."
if command -v python3 >/dev/null 2>&1; then
    PYTHON_VER=$(python3 --version 2>&1)
    TOOLS_STATUS["python3"]="INSTALLED"
    TOOLS_VERSION["python3"]="${PYTHON_VER}"
    log_success "Python 3 is installed: ${PYTHON_VER}"
else
    TOOLS_STATUS["python3"]="MISSING"
    TOOLS_VERSION["python3"]="Not Found"
    log_error "Python 3 is NOT installed!"
    INSTALL_PYTHON="sudo apt-get update && sudo apt-get install -y python3 python3-pip python3-venv python3-dev build-essential"
    prompt_or_install "Python 3" "${INSTALL_PYTHON}"
fi

# --- Tool 6: Pip ---
log_step "Checking Pip (pip3)..."
if command -v pip3 >/dev/null 2>&1 || command -v pip >/dev/null 2>&1; then
    PIP_BIN=$(command -v pip3 || command -v pip)
    PIP_VER=$("${PIP_BIN}" --version | awk '{print $1, $2}')
    TOOLS_STATUS["pip"]="INSTALLED"
    TOOLS_VERSION["pip"]="${PIP_VER}"
    log_success "Pip is installed: ${PIP_VER}"
else
    TOOLS_STATUS["pip"]="MISSING"
    TOOLS_VERSION["pip"]="Not Found"
    log_error "Pip is NOT installed!"
    INSTALL_PIP="sudo apt-get update && sudo apt-get install -y python3-pip"
    prompt_or_install "Pip" "${INSTALL_PIP}"
fi

# --- Tool 7: Helm ---
log_step "Checking Helm..."
if command -v helm >/dev/null 2>&1; then
    HELM_VER=$(helm version --short 2>/dev/null || helm version | head -n1)
    TOOLS_STATUS["helm"]="INSTALLED"
    TOOLS_VERSION["helm"]="${HELM_VER}"
    log_success "Helm is installed: ${HELM_VER}"
else
    TOOLS_STATUS["helm"]="MISSING"
    TOOLS_VERSION["helm"]="Not Found"
    log_error "Helm is NOT installed!"
    INSTALL_HELM="curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash"
    prompt_or_install "Helm" "${INSTALL_HELM}"
fi

# --- Tool 8: Trivy ---
log_step "Checking Trivy (Security Scanner)..."
if command -v trivy >/dev/null 2>&1; then
    TRIVY_VER=$(trivy --version | head -n1 | awk '{print $1, $2}')
    TOOLS_STATUS["trivy"]="INSTALLED"
    TOOLS_VERSION["trivy"]="${TRIVY_VER}"
    log_success "Trivy is installed: ${TRIVY_VER}"
else
    TOOLS_STATUS["trivy"]="MISSING"
    TOOLS_VERSION["trivy"]="Not Found"
    log_warn "Trivy is NOT installed (optional for CI/CD vulnerability scanning)."
    INSTALL_TRIVY="sudo apt-get install -y wget apt-transport-https gnupg lsb-release && \
wget -qO - https://aquasecurity.github.io/trivy-repo/deb/public.key | gpg --dearmor | sudo tee /usr/share/keyrings/trivy.gpg > /dev/null && \
echo \"deb [signed-by=/usr/share/keyrings/trivy.gpg] https://aquasecurity.github.io/trivy-repo/deb \$(lsb_release -sc) main\" | sudo tee -a /etc/apt/sources.list.d/trivy.list && \
sudo apt-get update && sudo apt-get install -y trivy || curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sudo sh -s -- -b /usr/local/bin"
    prompt_or_install "Trivy" "${INSTALL_TRIVY}"
fi

# If any essential tool is missing and dry-run is not active, warn
MISSING_COUNT=0
for t in docker minikube kubectl python3; do
    if [[ "${TOOLS_STATUS[$t]}" == "MISSING" ]]; then
        MISSING_COUNT=$((MISSING_COUNT + 1))
    fi
done

if [[ ${MISSING_COUNT} -gt 0 && "${DRY_RUN}" != "true" && "${AUTO_INSTALL}" != "true" ]]; then
    log_warn "${MISSING_COUNT} core tool(s) are missing. Please install them using the commands above or re-run with '--auto-install'."
fi

# ------------------------------------------------------------------------------
# Step 3: Minikube Cluster Provisioning
# ------------------------------------------------------------------------------
if [[ "${SKIP_CLUSTER}" == "true" ]]; then
    log_info "Skipping Minikube cluster setup (--skip-cluster flag passed)."
elif [[ "${DRY_RUN}" == "true" ]]; then
    log_info "Dry-run mode: skipping Minikube start."
elif ! command -v minikube >/dev/null 2>&1; then
    log_warn "Minikube binary not available. Skipping cluster bootstrap."
else
    log_title "STEP 3: Minikube Cluster Initialization"

    # Check if Minikube is already running
    MINIKUBE_STATUS=$(minikube status --format='{{.Host}}' 2>/dev/null || echo "Stopped")

    if [[ "${MINIKUBE_STATUS}" == "Running" ]]; then
        log_success "Minikube cluster is already RUNNING."
    else
        log_step "Starting Minikube cluster (driver: docker, cpus: 2, memory: 4096MB)..."
        minikube start \
            --driver=docker \
            --cpus=2 \
            --memory=4096 \
            --wait=all

        log_success "Minikube cluster started successfully."
    fi

    # Verify kubectl context points to minikube
    log_step "Configuring kubectl context to minikube..."
    kubectl config use-context minikube >/dev/null 2>&1 || true

    # Enable Addons
    log_title "STEP 4: Enabling Minikube Addons"
    ADDONS=("metrics-server" "dashboard" "ingress")

    for addon in "${ADDONS[@]}"; do
        log_step "Enabling addon: ${addon}..."
        minikube addons enable "${addon}"
        log_success "Addon '${addon}' enabled."
    done

    log_info "Verifying metrics-server readiness..."
    kubectl wait --namespace kube-system --for=condition=ready pod -l k8s-app=metrics-server --timeout=90s 2>/dev/null || log_warn "metrics-server pods are initializing..."
fi

# ------------------------------------------------------------------------------
# Step 4: Namespace Provisioning
# ------------------------------------------------------------------------------
if command -v kubectl >/dev/null 2>&1 && [[ "${SKIP_CLUSTER}" != "true" && "${DRY_RUN}" != "true" ]]; then
    log_title "STEP 5: Creating Application Namespace (devops-platform)"
    
    if kubectl get namespace devops-platform >/dev/null 2>&1; then
        log_info "Namespace 'devops-platform' already exists."
    else
        log_step "Creating Kubernetes namespace 'devops-platform'..."
        kubectl create namespace devops-platform
        kubectl label namespace devops-platform \
            app.kubernetes.io/part-of=devops-platform \
            app.kubernetes.io/managed-by=setup-script \
            --overwrite >/dev/null 2>&1
        log_success "Namespace 'devops-platform' created."
    fi
fi

# ------------------------------------------------------------------------------
# Step 5: Prometheus & Grafana Monitoring Stack via Helm
# ------------------------------------------------------------------------------
if [[ "${SKIP_MONITORING}" == "true" ]]; then
    log_info "Skipping Prometheus & Grafana monitoring stack (--skip-monitoring flag passed)."
elif [[ "${SKIP_CLUSTER}" == "true" || "${DRY_RUN}" == "true" ]]; then
    log_info "Skipping Helm monitoring install due to skip-cluster/dry-run mode."
elif ! command -v helm >/dev/null 2>&1; then
    log_warn "Helm is not installed. Skipping Prometheus/Grafana setup."
    log_info "Install Helm to enable automated observability stack deployment."
else
    log_title "STEP 6: Deploying Prometheus + Grafana Observability Stack"

    log_step "Adding Prometheus Community Helm repository..."
    helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
    helm repo update >/dev/null 2>&1
    log_success "Helm repositories synchronized."

    # Create monitoring namespace
    kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f - >/dev/null 2>&1

    log_step "Checking for existing Prometheus Stack deployment in 'monitoring'..."
    if helm status kube-prometheus-stack -n monitoring >/dev/null 2>&1; then
        log_info "kube-prometheus-stack release already installed in 'monitoring' namespace."
    else
        log_step "Installing kube-prometheus-stack (Prometheus + Grafana + AlertManager)..."
        # Deploy with lightweight resource requests suitable for 2-CPU minikube nodes
        helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
            --namespace monitoring \
            --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false \
            --set prometheus.prometheusSpec.podMonitorSelectorNilUsesHelmValues=false \
            --set prometheus.prometheusSpec.resources.requests.cpu=100m \
            --set prometheus.prometheusSpec.resources.requests.memory=256Mi \
            --set grafana.adminPassword=admin \
            --set grafana.resources.requests.cpu=50m \
            --set grafana.resources.requests.memory=128Mi \
            --wait --timeout=300s || log_warn "Helm install timed out or finished with warnings. Pods may still be pulling images."

        log_success "Prometheus + Grafana stack deployed to 'monitoring' namespace."
        log_info "Grafana credentials: username 'admin', password 'admin'"
    fi
fi

# ------------------------------------------------------------------------------
# Step 6: Status Summary & Verification Report
# ------------------------------------------------------------------------------
log_title "SYSTEM STATUS SUMMARY & NEXT STEPS"

echo -e "${BOLD}${WHITE}==============================================================================${NC}"
printf "${BOLD}%-16s %-14s %-45s${NC}\n" "COMPONENT" "STATUS" "VERSION / DETAILS"
echo -e "${BOLD}${WHITE}------------------------------------------------------------------------------${NC}"

for tool in docker minikube kubectl ansible python3 pip helm trivy; do
    STATUS="${TOOLS_STATUS[$tool]:-UNKNOWN}"
    VERSION="${TOOLS_VERSION[$tool]:-N/A}"
    if [[ "${STATUS}" == "INSTALLED" ]]; then
        printf "%-16s ${GREEN}%-14s${NC} %-45s\n" "${tool}" "${STATUS}" "${VERSION:0:44}"
    else
        printf "%-16s ${RED}%-14s${NC} %-45s\n" "${tool}" "${STATUS}" "${VERSION:0:44}"
    fi
done

echo -e "${BOLD}${WHITE}------------------------------------------------------------------------------${NC}"

if command -v minikube >/dev/null 2>&1 && [[ "${SKIP_CLUSTER}" != "true" ]]; then
    MK_STATUS=$(minikube status --format='{{.Host}}' 2>/dev/null || echo "Stopped")
    if [[ "${MK_STATUS}" == *"Running"* ]]; then
        MK_IP=$(minikube ip 2>/dev/null || echo "N/A")
        printf "%-16s %-14s %-45s\n" "minikube-cluster" "RUNNING" "IP: ${MK_IP} (Driver: docker)"
        if command -v kubectl >/dev/null 2>&1; then
            K8S_NODES=$(kubectl get nodes --no-headers 2>/dev/null | wc -l || echo "0")
            printf "%-16s %-14s %-45s\n" "k8s-nodes" "ACTIVE" "${K8S_NODES} node(s) available"
            
            NS_STATUS=$(kubectl get ns devops-platform -o jsonpath='{.status.phase}' 2>/dev/null || echo "Missing")
            printf "%-16s %-14s %-45s\n" "devops-platform" "${NS_STATUS}" "App Target Namespace"
        fi
    else
        printf "%-16s %-14s %-45s\n" "minikube-cluster" "STOPPED" "Run 'minikube start' to launch"
    fi
fi

echo -e "${BOLD}${WHITE}==============================================================================${NC}\n"

echo -e "${BOLD}${GREEN}✔ Environment setup completed successfully!${NC}\n"

echo -e "${BOLD}${CYAN}Useful Operations & Access Commands:${NC}"
echo -e "  1. Launch Minikube Dashboard:   ${BOLD}minikube dashboard${NC}"
echo -e "  2. Access Grafana UI:          ${BOLD}kubectl port-forward svc/kube-prometheus-stack-grafana -n monitoring 3000:80${NC} (admin/admin)"
echo -e "  3. Access Prometheus UI:       ${BOLD}kubectl port-forward svc/kube-prometheus-stack-prometheus -n monitoring 9090:9090${NC}"
echo -e "  4. Run Platform Demo:          ${BOLD}bash scripts/demo.sh${NC}"
echo -e "  5. Execute Load Test:          ${BOLD}python3 scripts/load_test.py --help${NC}"
echo -e "  6. Cleanup Cluster Resources:  ${BOLD}bash scripts/cleanup.sh${NC}\n"
