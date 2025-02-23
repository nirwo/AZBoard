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
import os

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
CACHE_DIR = 'cache'
CACHE_DURATION = 300  # Cache data for 5 minutes
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

def get_cache_file_path(vm_name, resource_group):
    """Generate cache file path for a VM"""
    return os.path.join(CACHE_DIR, f"{resource_group}_{vm_name}.cache")

def load_from_cache(vm_name, resource_group):
    """Load VM data from cache if it exists and is not expired"""
    cache_file = get_cache_file_path(vm_name, resource_group)
    try:
        if os.path.exists(cache_file):
            with open(cache_file, 'r') as f:
                cached_data = json.load(f)
                cache_time = datetime.fromisoformat(cached_data['cache_time'])
                
                # Check if cache is still valid
                if datetime.now() - cache_time < datetime.timedelta(minutes=CACHE_DURATION):
                    app.logger.info(f"Using cached data for VM {vm_name}")
                    return cached_data['data']
    except Exception as e:
        app.logger.error(f"Error reading cache: {str(e)}")
    return None

def save_to_cache(vm_name, resource_group, data):
    """Save VM data to cache"""
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)
    
    cache_file = get_cache_file_path(vm_name, resource_group)
    try:
        cache_data = {
            'cache_time': datetime.now().isoformat(),
            'data': data
        }
        with open(cache_file, 'w') as f:
            json.dump(cache_data, f)
        app.logger.info(f"Cached data for VM {vm_name}")
    except Exception as e:
        app.logger.error(f"Error writing cache: {str(e)}")

def get_vm_details(vm_name, resource_group):
    """Get detailed VM information including NICs and disks"""
    # Try to get data from cache first
    cached_data = load_from_cache(vm_name, resource_group)
    if cached_data:
        return cached_data
        
    try:
        app.logger.info(f"Getting details for VM {vm_name} in resource group {resource_group}")
        
        # Get VM details including IP addresses
        vm_command = f"az vm show --name {vm_name} --resource-group {resource_group} --show-details -d"
        vm_result = subprocess.run(vm_command, shell=True, capture_output=True, text=True)
        
        if vm_result.returncode != 0:
            app.logger.error(f"Failed to get VM details: {vm_result.stderr}")
            return None
        
        vm_data = json.loads(vm_result.stdout)
        
        # Get IP addresses using the simpler command
        ip_command = f"az vm list-ip-addresses -n {vm_name} -g {resource_group} -o json"
        ip_result = subprocess.run(ip_command, shell=True, capture_output=True, text=True)
        
        nics = []
        if ip_result.returncode == 0:
            ip_data = json.loads(ip_result.stdout)
            if ip_data and len(ip_data) > 0:
                network = ip_data[0].get('virtualMachine', {}).get('network', {})
                private_ips = network.get('privateIpAddresses', [])
                public_ips = network.get('publicIpAddresses', [])
                
                # Get NIC details for additional information
                for nic_ref in vm_data.get('networkProfile', {}).get('networkInterfaces', []):
                    nic_id = nic_ref.get('id', '')
                    if not nic_id:
                        continue
                    
                    nic_parts = nic_id.split('/')
                    nic_name = nic_parts[-1]
                    nic_resource_group = nic_parts[4] if len(nic_parts) > 4 else resource_group
                    
                    # Get NIC details for subnet information
                    nic_command = f"az network nic show -n {nic_name} -g {nic_resource_group}"
                    nic_result = subprocess.run(nic_command, shell=True, capture_output=True, text=True)
                    
                    if nic_result.returncode == 0:
                        nic_data = json.loads(nic_result.stdout)
                        ip_configs = nic_data.get('ipConfigurations', [])
                        
                        for i, ip_config in enumerate(ip_configs):
                            subnet_id = ip_config.get('subnet', {}).get('id', '')
                            subnet_parts = subnet_id.split('/') if subnet_id else []
                            vnet_name = subnet_parts[-3] if len(subnet_parts) > 3 else 'Not configured'
                            subnet_name = subnet_parts[-1] if subnet_parts else 'Not configured'
                            
                            private_ip = private_ips[i] if i < len(private_ips) else 'Not configured'
                            public_ip = public_ips[i].get('ipAddress') if i < len(public_ips) else 'Not configured'
                            
                            nics.append({
                                'name': nic_name,
                                'private_ip': private_ip,
                                'public_ip': public_ip,
                                'subnet': subnet_name,
                                'vnet': vnet_name,
                                'status': 'Configured',
                                'primary': ip_config.get('primary', False)
                            })
        
        # If no NICs were found through IP addresses, add a default entry
        if not nics:
            nics.append({
                'name': 'Default NIC',
                'private_ip': 'Not configured',
                'public_ip': 'Not configured',
                'subnet': 'Not configured',
                'vnet': 'Not configured',
                'status': 'No IP configuration'
            })
        
        # Get disk details
        disks = []
        os_disk = vm_data.get('storageProfile', {}).get('osDisk', {})
        if os_disk:
            disks.append({
                'name': os_disk.get('name', 'OS Disk'),
                'size_gb': os_disk.get('diskSizeGb'),
                'type': os_disk.get('managedDisk', {}).get('storageAccountType'),
                'is_os_disk': True
            })
        
        # Add data disks
        for data_disk in vm_data.get('storageProfile', {}).get('dataDisks', []):
            disks.append({
                'name': data_disk.get('name', 'Data Disk'),
                'size_gb': data_disk.get('diskSizeGb'),
                'type': data_disk.get('managedDisk', {}).get('storageAccountType'),
                'is_os_disk': False,
                'lun': data_disk.get('lun')
            })
        
        result = {
            'vm': vm_data,
            'nics': nics,
            'disks': disks
        }
        
        # Save the result to cache
        save_to_cache(vm_name, resource_group, result)
        
        return result
        
    except Exception as e:
        app.logger.error(f"Error getting VM details for {vm_name}: {str(e)}")
        return None

def get_storage_accounts():
    """Get all storage accounts"""
    try:
        command = "az storage account list"
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            return json.loads(result.stdout)
        return []
    except Exception as e:
        app.logger.error(f"Error getting storage accounts: {str(e)}")
        return []

def get_network_resources():
    """Get network resources (VNets, Subnets, NSGs)"""
    try:
        resources = {
            'vnets': [],
            'public_ips': [],
            'nsgs': []
        }
        
        # Get VNets
        vnet_command = "az network vnet list"
        vnet_result = subprocess.run(vnet_command, shell=True, capture_output=True, text=True)
        if vnet_result.returncode == 0:
            resources['vnets'] = json.loads(vnet_result.stdout)
        
        # Get Public IPs
        pip_command = "az network public-ip list"
        pip_result = subprocess.run(pip_command, shell=True, capture_output=True, text=True)
        if pip_result.returncode == 0:
            resources['public_ips'] = json.loads(pip_result.stdout)
        
        # Get NSGs
        nsg_command = "az network nsg list"
        nsg_result = subprocess.run(nsg_command, shell=True, capture_output=True, text=True)
        if nsg_result.returncode == 0:
            resources['nsgs'] = json.loads(nsg_result.stdout)
        
        return resources
    except Exception as e:
        app.logger.error(f"Error getting network resources: {str(e)}")
        return {'vnets': [], 'public_ips': [], 'nsgs': []}

def calculate_vm_cost(vm_size, region="eastus"):
    """Calculate estimated monthly cost for a VM"""
    # Azure VM pricing (USD per hour) - This is a simplified version
    # In production, you should use the Azure Retail Prices API
    pricing = {
        # B-series (burstable)
        'Standard_B1s': 0.0208,
        'Standard_B1ms': 0.0416,
        'Standard_B2s': 0.0832,
        'Standard_B2ms': 0.1664,
        'Standard_B4ms': 0.3328,
        'Standard_B8ms': 0.6656,
        # D-series v4 (general purpose)
        'Standard_D2_v4': 0.1008,
        'Standard_D4_v4': 0.2016,
        'Standard_D8_v4': 0.4032,
        'Standard_D16_v4': 0.8064,
        # E-series v4 (memory optimized)
        'Standard_E2_v4': 0.1260,
        'Standard_E4_v4': 0.2520,
        'Standard_E8_v4': 0.5040,
        'Standard_E16_v4': 1.0080,
        # F-series v2 (compute optimized)
        'Standard_F2s_v2': 0.0850,
        'Standard_F4s_v2': 0.1700,
        'Standard_F8s_v2': 0.3400,
        'Standard_F16s_v2': 0.6800,
        # Default fallback
        'default': 0.1000
    }
    
    # Get hourly cost
    hourly_cost = pricing.get(vm_size, pricing['default'])
    
    # Calculate monthly cost (assuming 730 hours per month)
    monthly_cost = hourly_cost * 730
    
    return monthly_cost

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/kpi')
def kpi():
    return render_template('kpi.html')

@app.route('/storage')
def storage():
    return render_template('storage.html')

@app.route('/network')
def network():
    return render_template('network.html')

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
        total_monthly_cost = instances["total_monthly_cost"]
        
        if resource_group:
            filtered_instances = [i for i in filtered_instances if i["resourceGroup"] == resource_group]
        
        if status:
            filtered_instances = [i for i in filtered_instances if status.lower() in i["status"].lower()]
            
        if vm_size:
            filtered_instances = [i for i in filtered_instances if i["size"] == vm_size]
            
        # Recalculate total cost for filtered instances
        filtered_monthly_cost = sum(instance["monthly_cost"] for instance in filtered_instances)
            
        return jsonify({
            "instances": filtered_instances,
            "totalCount": len(instances["instances"]),
            "filteredCount": len(filtered_instances),
            "total_monthly_cost": filtered_monthly_cost
        })
        
    except Exception as e:
        app.logger.error(f"Error fetching instances: {str(e)}")
        return jsonify({"error": "Failed to fetch instances"})

@app.route('/api/storage-data')
def get_storage_data():
    if not check_azure_login():
        return jsonify({"error": "Not logged in to Azure"})
    
    try:
        storage_accounts = get_storage_accounts()
        return jsonify({
            "storage_accounts": storage_accounts
        })
    except Exception as e:
        app.logger.error(f"Error getting storage data: {str(e)}")
        return jsonify({"error": "Failed to get storage data"})

@app.route('/api/network-data')
def get_network_data():
    if not check_azure_login():
        return jsonify({"error": "Not logged in to Azure"})
    
    try:
        network_resources = get_network_resources()
        return jsonify(network_resources)
    except Exception as e:
        app.logger.error(f"Error getting network data: {str(e)}")
        return jsonify({"error": "Failed to get network data"})

@cache.memoize(timeout=CACHE_DURATION)
def get_azure_instances():
    """Get Azure instances with detailed information"""
    if not check_azure_login():
        return {"error": "Not logged in to Azure"}
    
    try:
        app.logger.info("Fetching Azure instances data...")
        
        # Get VM list with all details
        vm_command = "az vm list --show-details -d --query '[].{name:name,resourceGroup:resourceGroup,powerState:powerState,size:hardwareProfile.vmSize,location:location,osType:storageProfile.osDisk.osType}'"
        app.logger.info(f"Running VM list command: {vm_command}")
        vm_result = subprocess.run(vm_command, shell=True, capture_output=True, text=True)
        
        if vm_result.returncode != 0:
            app.logger.error(f"Failed to fetch VM list: {vm_result.stderr}")
            return {"error": "Failed to fetch VM list"}
        
        vms = json.loads(vm_result.stdout)
        instances = []
        total_cost = 0
        
        for vm in vms:
            vm_name = vm.get('name')
            resource_group = vm.get('resourceGroup')
            
            app.logger.info(f"Processing VM: {vm_name} in resource group {resource_group}")
            
            # Get detailed information
            details = get_vm_details(vm_name, resource_group)
            if not details:
                app.logger.warning(f"Could not get details for VM {vm_name}")
                continue
            
            # Calculate cost
            monthly_cost = calculate_vm_cost(vm.get('size'))
            total_cost += monthly_cost
            
            instance = {
                'name': vm_name,
                'resourceGroup': resource_group,
                'status': vm.get('powerState', 'Unknown'),
                'size': vm.get('size'),
                'location': vm.get('location'),
                'osType': vm.get('osType'),
                'monthly_cost': monthly_cost,
                'nics': details['nics'],
                'disks': details['disks']
            }
            
            app.logger.info(f"Instance details: {json.dumps(instance)}")
            instances.append(instance)
        
        result = {
            "instances": instances,
            "total_monthly_cost": total_cost
        }
        
        app.logger.info(f"Total instances found: {len(instances)}")
        return result
        
    except Exception as e:
        app.logger.error(f"Error fetching instances: {str(e)}")
        return {"error": f"Failed to fetch instances: {str(e)}"}

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
