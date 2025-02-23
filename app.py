from flask import Flask, render_template, jsonify, request, session
from flask_caching import Cache
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from apscheduler.schedulers.background import BackgroundScheduler
import json
import subprocess
import re
import humanize
from datetime import datetime
import logging
from logging.handlers import RotatingFileHandler
import time
import secrets

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)

# Configure logging
logging.basicConfig(level=logging.INFO)
handler = RotatingFileHandler('azure_dashboard.log', maxBytes=10000000, backupCount=5)
handler.setFormatter(logging.Formatter(
    '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
))
app.logger.addHandler(handler)

# Configure caching
cache = Cache(app, config={'CACHE_TYPE': 'simple'})

# Configure rate limiting
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

# Global variable to store the last refresh time
last_refresh_time = None

def check_azure_login():
    try:
        cmd = "az account show"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        return result.returncode == 0
    except Exception:
        return False

def get_azure_login_url():
    try:
        cmd = "az login --only-show-url"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception as e:
        app.logger.error(f"Error getting Azure login URL: {str(e)}")
    return None

@app.route('/api/check-login')
def check_login():
    is_logged_in = check_azure_login()
    if is_logged_in:
        return jsonify({"status": "logged_in"})
    else:
        login_url = get_azure_login_url()
        return jsonify({
            "status": "not_logged_in",
            "login_url": login_url
        })

def get_vm_status(vm_name, resource_group):
    try:
        status_command = f"az vm get-instance-view --name {vm_name} --resource-group {resource_group} --query instanceView.statuses[1].displayStatus -o tsv"
        status_result = subprocess.run(status_command, shell=True, capture_output=True, text=True)
        return status_result.stdout.strip() if status_result.returncode == 0 else 'Unknown'
    except Exception:
        return 'Unknown'

def get_vm_size(vm_name, resource_group):
    try:
        size_command = f"az vm show --name {vm_name} --resource-group {resource_group} --query hardwareProfile.vmSize -o tsv"
        size_result = subprocess.run(size_command, shell=True, capture_output=True, text=True)
        return size_result.stdout.strip() if size_result.returncode == 0 else 'Unknown'
    except Exception:
        return 'Unknown'

@cache.memoize(timeout=300)  # Cache for 5 minutes
def get_azure_instances():
    global last_refresh_time
    
    if not check_azure_login():
        return {"error": "Not logged in to Azure"}
    
    try:
        app.logger.info("Fetching Azure instances data...")
        
        # Simulate progress for long-running operation
        def report_progress(progress):
            return {"status": "progress", "progress": progress}
        
        # Get VM list with extended information
        vm_command = """
        az vm list --query '[].{
            name:name,
            resourceGroup:resourceGroup,
            privateIps:privateIps,
            subnet:networkProfile.networkInterfaces[0].id,
            location:location,
            osType:storageProfile.osDisk.osType,
            tags:tags
        }' -o json
        """
        vm_result = subprocess.run(vm_command, shell=True, capture_output=True, text=True)
        
        if vm_result.returncode != 0:
            app.logger.error(f"Error fetching VM data: {vm_result.stderr}")
            return {"error": "Failed to fetch VM data"}
        
        vms = json.loads(vm_result.stdout)
        total_vms = len(vms)
        instances = []
        
        for idx, vm in enumerate(vms, 1):
            # Extract subnet name from the network interface ID
            subnet_match = re.search(r'/subnets/([^/]+)', vm['subnet']) if vm['subnet'] else None
            subnet_name = subnet_match.group(1) if subnet_match else 'N/A'
            
            # Get VM status and size
            status = get_vm_status(vm['name'], vm['resourceGroup'])
            size = get_vm_size(vm['name'], vm['resourceGroup'])
            
            instances.append({
                'name': vm['name'],
                'resourceGroup': vm['resourceGroup'],
                'ipAddress': vm['privateIps'][0] if vm['privateIps'] else 'N/A',
                'subnet': subnet_name,
                'location': vm['location'],
                'status': status,
                'size': size,
                'osType': vm['osType'],
                'tags': vm['tags'] if vm['tags'] else {}
            })
            
            # Report progress
            progress = int((idx / total_vms) * 100)
            app.logger.info(f"Processing VM {idx}/{total_vms} ({progress}%)")
        
        last_refresh_time = datetime.now()
        app.logger.info(f"Successfully fetched data for {len(instances)} instances")
        return {"status": "complete", "instances": instances}
    except Exception as e:
        app.logger.error(f"Error in get_azure_instances: {str(e)}")
        return {"error": str(e)}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/kpi')
def kpi():
    return render_template('kpi.html')

@app.route('/api/kpi-data')
def get_kpi_data():
    if not check_azure_login():
        return jsonify({"error": "Not logged in to Azure"})
    
    try:
        # Get instance data
        instances = get_azure_instances()
        if "error" in instances:
            return jsonify(instances)

        # Process data for KPI charts
        resource_groups = {}
        vm_sizes = {}
        status_counts = {"running": 0, "stopped": 0, "other": 0}
        
        for instance in instances["instances"]:
            # Resource Group stats
            rg = instance["resourceGroup"]
            resource_groups[rg] = resource_groups.get(rg, 0) + 1
            
            # VM Size stats
            size = instance["size"]
            vm_sizes[size] = vm_sizes.get(size, 0) + 1
            
            # Status stats
            status = instance["status"].lower()
            if "running" in status:
                status_counts["running"] += 1
            elif "stopped" in status or "deallocated" in status:
                status_counts["stopped"] += 1
            else:
                status_counts["other"] += 1

        # Mock data for demonstration
        kpi_data = {
            # Cost Analysis
            "costLabels": ["Jan", "Feb", "Mar", "Apr", "May", "Jun"],
            "costData": [1200, 1350, 1100, 1400, 1300, 1500],
            
            # Resource Utilization
            "utilizationLabels": list(resource_groups.keys()),
            "cpuData": [75, 60, 85, 70, 90],
            "memoryData": [65, 70, 75, 80, 85],
            
            # Resource Group Distribution
            "resourceGroups": list(resource_groups.keys()),
            "resourceGroupCounts": list(resource_groups.values()),
            
            # VM Size Distribution
            "vmSizes": list(vm_sizes.keys()),
            "vmSizeCounts": list(vm_sizes.values()),
            
            # Status Distribution
            "statusLabels": ["Running", "Stopped", "Other"],
            "statusCounts": [
                status_counts["running"],
                status_counts["stopped"],
                status_counts["other"]
            ],
            
            # Performance Metrics
            "metrics": [
                {
                    "name": "Avg CPU Utilization",
                    "current": "75%",
                    "target": "80%",
                    "status": "good",
                    "trend": "up",
                    "trendValue": 5
                },
                {
                    "name": "Avg Memory Usage",
                    "current": "65%",
                    "target": "70%",
                    "status": "good",
                    "trend": "up",
                    "trendValue": 3
                },
                {
                    "name": "Running Instances",
                    "current": str(status_counts["running"]),
                    "target": str(len(instances["instances"])),
                    "status": "warning" if status_counts["stopped"] > 0 else "good",
                    "trend": "up" if status_counts["running"] > status_counts["stopped"] else "down",
                    "trendValue": int((status_counts["running"] / len(instances["instances"])) * 100)
                }
            ]
        }
        
        return jsonify(kpi_data)
        
    except Exception as e:
        app.logger.error(f"Error generating KPI data: {str(e)}")
        return jsonify({"error": "Failed to generate KPI data"})

@app.route('/api/instances')
@limiter.limit("30/minute")
def get_instances():
    try:
        instances = get_azure_instances()
        if "error" in instances:
            return jsonify(instances)
            
        # Add additional filtering capabilities
        filter_type = request.args.get('filter', 'all')
        resource_group = request.args.get('resource_group')
        status = request.args.get('status')
        vm_size = request.args.get('vm_size')
        
        filtered_instances = instances["instances"]
        
        if resource_group:
            filtered_instances = [i for i in filtered_instances if i["resourceGroup"] == resource_group]
        
        if status:
            filtered_instances = [i for i in filtered_instances if status.lower() in i["status"].lower()]
            
        if vm_size:
            filtered_instances = [i for i in filtered_instances if i["size"] == vm_size]
            
        return jsonify({
            "instances": filtered_instances,
            "totalCount": len(instances["instances"]),
            "filteredCount": len(filtered_instances)
        })
        
    except Exception as e:
        app.logger.error(f"Error fetching instances: {str(e)}")
        return jsonify({"error": "Failed to fetch instances"})

@app.route('/api/refresh', methods=['POST'])
@limiter.limit("6/minute")
def refresh_data():
    cache.delete_memoized(get_azure_instances)
    result = get_azure_instances()
    if "error" in result:
        return jsonify(result), 400
    
    refresh_time = humanize.naturaltime(last_refresh_time) if last_refresh_time else 'Never'
    if "instances" in result:
        return jsonify({
            "status": "complete",
            "instances": result["instances"],
            "lastRefresh": refresh_time,
            "totalCount": len(result["instances"])
        })
    return jsonify(result)

def init_scheduler():
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=lambda: cache.delete_memoized(get_azure_instances),
                     trigger="interval", minutes=5)
    scheduler.start()

if __name__ == '__main__':
    init_scheduler()
    app.run(debug=True, port=8080, host='0.0.0.0')
