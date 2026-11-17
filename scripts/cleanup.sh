#!/usr/bin/env bash
# ==============================================================================
# Intelligent Autonomous DevOps Deployment Platform - Cleanup Script
# Description: Teardown and resource reclamation script for Kubernetes workloads,
#              devops-platform namespace, Docker images, and optional Minikube stop.
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
NC='\033[0m'

# ------------------------------------------------------------------------------
# Logging Helpers
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
# CLI Options & Defaults
# ------------------------------------------------------------------------------
FORCE=false
STOP_MINIKUBE=false
DELETE_MINIKUBE=false
KEEP_IMAGES=false
ALL=false

show_help() {
    cat << EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  -f, --force            Non-interactive mode; bypass confirmation prompts
  --stop-minikube        Stop the Minikube cluster after deleting application resources
  --delete-minikube      Completely delete the Minikube virtual cluster
  --keep-images          Preserve locally built Docker container images
  --all                  Perform complete teardown: purge K8s, stop Minikube, remove images
  -h, --help             Display this help message and exit

Description:
  Safely tears down application resources in the 'devops-platform' namespace,
  deletes the namespace, purges local and Minikube Docker images, and optionally
  stops or deletes the Minikube cluster.
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -f|--force)
            FORCE=true
            shift
            ;;
        --stop-minikube)
            STOP_MINIKUBE=true
            shift
            ;;
        --delete-minikube)
            DELETE_MINIKUBE=true
            shift
            ;;
        --keep-images)
            KEEP_IMAGES=true
            shift
            ;;
        --all)
            ALL=true
            FORCE=true
            STOP_MINIKUBE=true
            shift
            ;;
        -h|--help)
            show_help
            ;;
        *)
            log_error "Unknown option: $1"
            echo "Use --help for usage details."
            exit 1
            ;;
    esac
done

# ------------------------------------------------------------------------------
# Banner & Confirmation
# ------------------------------------------------------------------------------
echo -e "${BOLD}${CYAN}"
cat << "EOF"
   ___ _                             
  / __| |___ __ _ _ _  _  _ _ __     
 | (__| / -_) _` | ' \| || | '_ \    
  \___|_\___\__,_|_||_|\_,_| .__/    
                           |_|       
  DevOps Platform Teardown & Resource Cleanup
EOF
echo -e "${NC}"

if [[ "${FORCE}" != "true" ]]; then
    echo -e "${YELLOW}This script will remove resources in namespace 'devops-platform'.${NC}"
    read -r -p "Are you sure you want to proceed with cleanup? [y/N]: " CONFIRM
    if [[ ! "${CONFIRM}" =~ ^[Yy]$ ]]; then
        log_info "Cleanup operation aborted by user."
        exit 0
    fi
fi

# Track items cleaned for summary
declare -a CLEANUP_ACTIONS

# ------------------------------------------------------------------------------
# Step 1: Terminate Background Port-Forward or AI Engine Processes
# ------------------------------------------------------------------------------
log_title "STEP 1: Terminating Background Processes"

pkill -f "kubectl port-forward.*devops-demo-app" 2>/dev/null && {
    log_success "Terminated background kubectl port-forward processes."
    CLEANUP_ACTIONS+=("K8s Port-Forward: Terminated")
} || log_info "No background kubectl port-forward processes running."

pkill -f "python3.*ai-engine/main.py" 2>/dev/null && {
    log_success "Terminated running AI Decision Engine instances."
    CLEANUP_ACTIONS+=("AI Decision Engine: Stopped")
} || log_info "No background AI Engine processes active."

# ------------------------------------------------------------------------------
# Step 2: Delete Kubernetes Resources in devops-platform
# ------------------------------------------------------------------------------
log_title "STEP 2: Deleting Kubernetes Resources (Namespace: devops-platform)"

if command -v kubectl >/dev/null 2>&1; then
    if kubectl get namespace devops-platform >/dev/null 2>&1; then
        log_step "Deleting Deployments, Services, HPAs, Ingresses, and Pods..."
        kubectl delete all,hpa,ingress,configmap,secret --all -n devops-platform --timeout=60s --ignore-not-found=true >/dev/null 2>&1 || true
        log_success "Resources in 'devops-platform' deleted."

        log_step "Deleting namespace 'devops-platform'..."
        kubectl delete namespace devops-platform --timeout=90s --ignore-not-found=true >/dev/null 2>&1 || true
        log_success "Namespace 'devops-platform' purged."
        CLEANUP_ACTIONS+=("devops-platform Namespace: Purged")
    else
        log_info "Namespace 'devops-platform' does not exist. Nothing to remove."
        CLEANUP_ACTIONS+=("devops-platform Namespace: Already Clean")
    fi
else
    log_warn "kubectl not found. Skipping Kubernetes resource deletion."
fi

# ------------------------------------------------------------------------------
# Step 3: Remove Local & Minikube Docker Images
# ------------------------------------------------------------------------------
log_title "STEP 3: Cleaning Docker Images"

if [[ "${KEEP_IMAGES}" == "true" ]]; then
    log_info "Preserving Docker images (--keep-images specified)."
    CLEANUP_ACTIONS+=("Docker Images: Preserved")
elif command -v docker >/dev/null 2>&1; then
    # Find devops-platform images
    IMAGE_MATCHES=$(docker images --format '{{.Repository}}:{{.Tag}}' | grep -E 'devops-demo-app|devops-platform' || true)

    if [[ -n "${IMAGE_MATCHES}" ]]; then
        log_step "Removing local platform images..."
        while IFS= read -r img; do
            if [[ -n "$img" ]]; then
                docker rmi -f "${img}" >/dev/null 2>&1 || true
                log_success "Removed image: ${img}"
            fi
        done <<< "${IMAGE_MATCHES}"
        CLEANUP_ACTIONS+=("Docker Local Images: Removed")
    else
        log_info "No local platform Docker images found to delete."
        CLEANUP_ACTIONS+=("Docker Local Images: None Found")
    fi

    # Remove inside Minikube if Minikube is running
    if command -v minikube >/dev/null 2>&1; then
        if minikube status --format='{{.Host}}' 2>/dev/null | grep -q "Running"; then
            log_step "Removing cached images from Minikube..."
            minikube image rm devops-demo-app:v1.0 >/dev/null 2>&1 || true
            minikube image rm devops-demo-app:latest >/dev/null 2>&1 || true
            CLEANUP_ACTIONS+=("Minikube Image Cache: Cleared")
        fi
    fi
else
    log_warn "Docker is not available. Skipping image removal."
fi

# ------------------------------------------------------------------------------
# Step 4: Optional Minikube Stop or Delete
# ------------------------------------------------------------------------------
log_title "STEP 4: Minikube Cluster State"

if command -v minikube >/dev/null 2>&1; then
    MK_STATUS=$(minikube status --format='{{.Host}}' 2>/dev/null || echo "Stopped")

    if [[ "${DELETE_MINIKUBE}" == "true" ]]; then
        log_step "Deleting Minikube cluster (--delete-minikube specified)..."
        minikube delete
        log_success "Minikube cluster destroyed."
        CLEANUP_ACTIONS+=("Minikube Cluster: Deleted")
    elif [[ "${STOP_MINIKUBE}" == "true" ]]; then
        if [[ "${MK_STATUS}" == *"Running"* ]]; then
            log_step "Stopping Minikube cluster..."
            minikube stop
            log_success "Minikube cluster stopped."
            CLEANUP_ACTIONS+=("Minikube Cluster: Stopped")
        else
            log_info "Minikube is already stopped."
            CLEANUP_ACTIONS+=("Minikube Cluster: Already Stopped")
        fi
    elif [[ "${FORCE}" != "true" && "${MK_STATUS}" == *"Running"* ]]; then
        read -r -p "Would you like to stop the Minikube cluster to free system resources? [y/N]: " STOP_CONFIRM
        if [[ "${STOP_CONFIRM}" =~ ^[Yy]$ ]]; then
            log_step "Stopping Minikube cluster..."
            minikube stop
            log_success "Minikube cluster stopped."
            CLEANUP_ACTIONS+=("Minikube Cluster: Stopped")
        else
            log_info "Leaving Minikube cluster running for future sessions."
            CLEANUP_ACTIONS+=("Minikube Cluster: Kept Running")
        fi
    else
        log_info "Minikube cluster status: ${MK_STATUS}"
        CLEANUP_ACTIONS+=("Minikube Cluster: ${MK_STATUS}")
    fi
fi

# ------------------------------------------------------------------------------
# Step 5: Summary Report
# ------------------------------------------------------------------------------
log_title "CLEANUP VERIFICATION & SUMMARY"

echo -e "${BOLD}${WHITE}==============================================================================${NC}"
printf "${BOLD}%-35s %-40s${NC}\n" "ACTION ITEM" "RESULT"
echo -e "${BOLD}${WHITE}------------------------------------------------------------------------------${NC}"

for item in "${CLEANUP_ACTIONS[@]}"; do
    ITEM_NAME="${item%%:*}"
    ITEM_RES="${item##*: }"
    printf "%-35s ${GREEN}%-40s${NC}\n" "${ITEM_NAME}" "${ITEM_RES}"
done

echo -e "${BOLD}${WHITE}==============================================================================${NC}\n"

echo -e "${BOLD}${GREEN}✔ Cleanup process completed successfully.${NC}"
echo -e "To restart the platform at any time, run: ${BOLD}${CYAN}bash scripts/setup.sh${NC}\n"
