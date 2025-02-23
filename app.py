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

def get_vm_details(vm_name, resource_group):
    """Get detailed VM information including NICs and disks"""
    try:
        # Get VM details
        vm_command = f"az vm show --name {vm_name} --resource-group {resource_group} -d"
        vm_result = subprocess.run(vm_command, shell=True, capture_output=True, text=True)
        if vm_result.returncode != 0:
            return None
        
        vm_data = json.loads(vm_result.stdout)
        
        # Get NIC details
        nics = []
        for nic_ref in vm_data.get('networkProfile', {}).get('networkInterfaces', []):
            nic_id = nic_ref.get('id', '')
            if not nic_id:
                continue
                
            # Extract NIC name and resource group from the ID
            nic_parts = nic_id.split('/')
            nic_name = nic_parts[-1]
            nic_resource_group = nic_parts[4] if len(nic_parts) > 4 else resource_group
            
            nic_command = f"az network nic show --name {nic_name} --resource-group {nic_resource_group}"
            nic_result = subprocess.run(nic_command, shell=True, capture_output=True, text=True)
            
            if nic_result.returncode == 0:
                nic_data = json.loads(nic_result.stdout)
                ip_configs = nic_data.get('ipConfigurations', [])
                
                for ip_config in ip_configs:
                    private_ip = ip_config.get('privateIpAddress')
                    subnet_id = ip_config.get('subnet', {}).get('id', '')
                    subnet_name = subnet_id.split('/')[-1] if subnet_id else None
                    
                    # Get public IP if it exists
                    public_ip = None
                    public_ip_id = ip_config.get('publicIpAddress', {}).get('id')
                    if public_ip_id:
                        pip_parts = public_ip_id.split('/')
                        pip_name = pip_parts[-1]
                        pip_resource_group = pip_parts[4] if len(pip_parts) > 4 else resource_group
                        pip_command = f"az network public-ip show --name {pip_name} --resource-group {pip_resource_group}"
                        pip_result = subprocess.run(pip_command, shell=True, capture_output=True, text=True)
                        if pip_result.returncode == 0:
                            pip_data = json.loads(pip_result.stdout)
                            public_ip = pip_data.get('ipAddress')
                    
                    nics.append({
                        'name': nic_data.get('name'),
                        'private_ip': private_ip,
                        'public_ip': public_ip,
                        'subnet': subnet_name,
                        'mac_address': nic_data.get('macAddress'),
                        'enable_ip_forwarding': nic_data.get('enableIpForwarding', False),
                        'primary': ip_config.get('primary', False)
                    })
        
        # Get disk details
        disks = []
        os_disk = vm_data.get('storageProfile', {}).get('osDisk', {})
        os_disk_id = os_disk.get('managedDisk', {}).get('id', '')
        
        if os_disk_id:
            disk_parts = os_disk_id.split('/')
            disk_name = disk_parts[-1]
            disk_resource_group = disk_parts[4] if len(disk_parts) > 4 else resource_group
            
            disk_command = f"az disk show --name {disk_name} --resource-group {disk_resource_group}"
            disk_result = subprocess.run(disk_command, shell=True, capture_output=True, text=True)
            if disk_result.returncode == 0:
                disk_data = json.loads(disk_result.stdout)
                disks.append({
                    'name': disk_data.get('name'),
                    'size_gb': disk_data.get('diskSizeGb'),
                    'type': disk_data.get('sku', {}).get('name'),
                    'is_os_disk': True,
                    'caching': os_disk.get('caching'),
                    'storage_account_type': disk_data.get('sku', {}).get('name')
                })
        
        # Get data disks
        for data_disk in vm_data.get('storageProfile', {}).get('dataDisks', []):
            disk_id = data_disk.get('managedDisk', {}).get('id', '')
            if disk_id:
                disk_parts = disk_id.split('/')
                disk_name = disk_parts[-1]
                disk_resource_group = disk_parts[4] if len(disk_parts) > 4 else resource_group
                
                disk_command = f"az disk show --name {disk_name} --resource-group {disk_resource_group}"
                disk_result = subprocess.run(disk_command, shell=True, capture_output=True, text=True)
                if disk_result.returncode == 0:
                    disk_data = json.loads(disk_result.stdout)
                    disks.append({
                        'name': disk_data.get('name'),
                        'size_gb': disk_data.get('diskSizeGb'),
                        'type': disk_data.get('sku', {}).get('name'),
                        'is_os_disk': False,
                        'lun': data_disk.get('lun'),
                        'caching': data_disk.get('caching'),
                        'storage_account_type': disk_data.get('sku', {}).get('name')
                    })
        
        return {
            'vm': vm_data,
            'nics': nics,
            'disks': disks
        }
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

@cache.memoize(timeout=300)
def get_azure_instances():
    """Get Azure instances with detailed information"""
    if not check_azure_login():
        return {"error": "Not logged in to Azure"}
    
    try:
        app.logger.info("Fetching Azure instances data...")
        
        # Get VM list
        vm_command = "az vm list --show-details -d"
        vm_result = subprocess.run(vm_command, shell=True, capture_output=True, text=True)
        if vm_result.returncode != 0:
            return {"error": "Failed to fetch VM list"}
        
        vms = json.loads(vm_result.stdout)
        instances = []
        total_cost = 0
        
        for vm in vms:
            # Get detailed information
            details = get_vm_details(vm.get('name'), vm.get('resourceGroup'))
            if not details:
                continue
            
            # Calculate cost
            monthly_cost = calculate_vm_cost(vm.get('hardwareProfile', {}).get('vmSize'))
            total_cost += monthly_cost
            
            instance = {
                'name': vm.get('name'),
                'resourceGroup': vm.get('resourceGroup'),
                'status': vm.get('powerState', 'Unknown'),
                'size': vm.get('hardwareProfile', {}).get('vmSize'),
                'location': vm.get('location'),
                'osType': vm.get('storageProfile', {}).get('osDisk', {}).get('osType'),
                'tags': vm.get('tags', {}),
                'monthly_cost': monthly_cost,
                'nics': details['nics'],
                'disks': details['disks']
            }
            instances.append(instance)
        
        return {
            "instances": instances,
            "total_monthly_cost": total_cost
        }
        
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
