from flask import Flask, render_template, jsonify, request
from azure.identity import AzureCliCredential
from azure.mgmt.compute import ComputeManagementClient
from logging.handlers import RotatingFileHandler
import os
import json
import subprocess
import logging
from datetime import datetime, timedelta
import random
import sys
import platform

app = Flask(__name__)

# Configure logging with Windows-compatible paths
log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
if not os.path.exists(log_dir):
    os.makedirs(log_dir)
file_handler = RotatingFileHandler(os.path.join(log_dir, 'azure_dashboard.log'), maxBytes=10240, backupCount=10)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
))
file_handler.setLevel(logging.INFO)
app.logger.addHandler(file_handler)
app.logger.setLevel(logging.INFO)
app.logger.info('Azure Dashboard startup')

# Cache configuration with Windows-compatible paths
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache')
CACHE_DURATION = timedelta(minutes=5)  # Cache data for 5 minutes
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)
VM_LIST_CACHE_FILE = os.path.join(CACHE_DIR, 'vm_list.cache')

SUBSCRIPTION_ID = "7e932c10-bfc9-4861-8403-11bc5cabc659"

def get_vm_list_from_azure(subscription=None):
    """Get list of VMs directly from Azure"""
    try:
        credential = AzureCliCredential()
        compute_client = ComputeManagementClient(credential, subscription if subscription else SUBSCRIPTION_ID)
        
        vms = []
        for vm in compute_client.virtual_machines.list_all():
            vm_instance = compute_client.virtual_machines.instance_view(
                vm.id.split('/')[4],  # resource group
                vm.name
            )
            
            power_state = "Unknown"
            if vm_instance.statuses:
                for status in vm_instance.statuses:
                    if status.code.startswith("PowerState/"):
                        power_state = status.code.split("/")[1]
                        break
            
            vm_dict = vm.as_dict()
            vms.append({
                'name': vm_dict['name'],
                'resourceGroup': vm_dict['id'].split('/')[4],
                'location': vm_dict['location'],
                'vmSize': vm_dict['hardware_profile']['vm_size'],
                'powerState': power_state,
                'tags': vm_dict.get('tags', {})
            })
        
        return vms
    except Exception as e:
        app.logger.error(f"Error getting VM list: {str(e)}")
        raise Exception(f"Error getting VM list: {str(e)}")

def load_vm_list_from_cache():
    """Load VM list from cache if it exists and is not expired"""
    try:
        if os.path.exists(VM_LIST_CACHE_FILE):
            with open(VM_LIST_CACHE_FILE, 'r') as f:
                cached_data = json.load(f)
                cache_time = datetime.fromisoformat(cached_data['cache_time'])
                
                # Check if cache is still valid
                if datetime.now() - cache_time < CACHE_DURATION:
                    app.logger.info("Using cached VM list")
                    return cached_data['data'], cached_data.get('vm_states', {})
    except Exception as e:
        app.logger.error(f"Error reading VM list cache: {str(e)}")
    return None, {}

def save_vm_list_to_cache(vms, vm_states):
    """Save VM list to cache with state tracking"""
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)
    
    try:
        cache_data = {
            'cache_time': datetime.now().isoformat(),
            'data': vms,
            'vm_states': vm_states
        }
        with open(VM_LIST_CACHE_FILE, 'w') as f:
            json.dump(cache_data, f)
        app.logger.info("Cached VM list")
    except Exception as e:
        app.logger.error(f"Error writing VM list cache: {str(e)}")

def get_vm_state_hash(vm):
    """Generate a hash of VM state for change detection"""
    # Convert the VM state to a sorted JSON string to ensure consistent hashing
    vm_json = json.dumps(vm, sort_keys=True)
    # Return the string directly - it's already JSON-serializable
    return vm_json

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
                if datetime.now() - cache_time < CACHE_DURATION:
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

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/kpi')
def kpi():
    return render_template('kpi.html')

@app.route('/api/vms')
def get_vms():
    """Get paginated list of VMs with change detection"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        subscription = request.args.get('subscription', '')
        force_refresh = request.args.get('force_refresh', '').lower() == 'true'
        
        # Try to load from cache first
        cached_vms, vm_states = load_vm_list_from_cache() if not force_refresh else (None, {})
        
        if cached_vms is None:
            # Cache miss or force refresh, get fresh data from Azure
            vms = get_vm_list_from_azure(subscription)
            new_vm_states = {vm['name']: get_vm_state_hash(vm) for vm in vms}
            save_vm_list_to_cache(vms, new_vm_states)
        else:
            # Cache hit, check for changes
            try:
                current_vms = get_vm_list_from_azure(subscription)
                new_vm_states = {vm['name']: get_vm_state_hash(vm) for vm in current_vms}
                
                # Detect changes
                changed_vms = []
                for vm in current_vms:
                    name = vm['name']
                    if name not in vm_states or vm_states[name] != new_vm_states[name]:
                        changed_vms.append(name)
                
                if changed_vms:
                    app.logger.info(f"Detected changes in VMs: {changed_vms}")
                    vms = current_vms
                    save_vm_list_to_cache(vms, new_vm_states)
                else:
                    vms = cached_vms
            except Exception as e:
                app.logger.warning(f"Failed to check for VM changes: {str(e)}")
                vms = cached_vms
        
        # Calculate pagination
        total = len(vms)
        start_idx = (page - 1) * per_page
        end_idx = min(start_idx + per_page, total)
        current_page_vms = vms[start_idx:end_idx]
        
        return jsonify({
            'vms': current_page_vms,
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': (total + per_page - 1) // per_page
        })
        
    except Exception as e:
        app.logger.error(f"Error in get_vms: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/vms/batch')
def get_vms_batch():
    """Get batch of VM details"""
    try:
        vm_list = request.args.getlist('vms[]')  # Format: resourceGroup/vmName
        if not vm_list:
            return jsonify({'error': 'No VMs specified'}), 400
            
        batch_data = {}
        
        for vm_info in vm_list:
            try:
                resource_group, vm_name = vm_info.split('/')
            except ValueError:
                app.logger.error(f"Invalid VM info format: {vm_info}")
                continue
            
            # Check cache first
            cached_data = load_from_cache(vm_name, resource_group)
            if cached_data:
                batch_data[vm_info] = cached_data
                continue
                
            try:
                credential = AzureCliCredential()
                compute_client = ComputeManagementClient(credential, SUBSCRIPTION_ID)
                
                # Get VM details
                vm = compute_client.virtual_machines.get(resource_group, vm_name, expand='instanceView')
                vm_dict = vm.as_dict()
                
                # Get IP addresses
                nics = []
                for nic in vm_dict['properties']['networkProfile']['networkInterfaces']:
                    nic_name = nic['id'].split('/')[-1]
                    nic_resource_group = nic['id'].split('/')[4]
                    
                    # Get NIC details for subnet information
                    nic_client = compute_client.network_interfaces.get(nic_resource_group, nic_name)
                    nic_dict = nic_client.as_dict()
                    
                    ip_configs = nic_dict['properties']['ipConfigurations']
                    
                    for i, ip_config in enumerate(ip_configs):
                        subnet_id = ip_config['subnet']['id']
                        subnet_parts = subnet_id.split('/') if subnet_id else []
                        vnet_name = subnet_parts[-3] if len(subnet_parts) > 3 else 'Not configured'
                        subnet_name = subnet_parts[-1] if subnet_parts else 'Not configured'
                        
                        private_ip = ip_config['properties']['privateIPAddress']
                        public_ip = ip_config['properties'].get('publicIPAddress', {}).get('ipAddress') if ip_config['properties'].get('publicIPAddress') else 'Not configured'
                        
                        nics.append({
                            'name': nic_name,
                            'private_ip': private_ip,
                            'public_ip': public_ip,
                            'subnet': subnet_name,
                            'vnet': vnet_name,
                            'status': 'Configured',
                            'primary': ip_config['properties']['primary']
                        })
                
                # Get disk details
                disks = []
                os_disk = vm_dict['properties']['storageProfile']['osDisk']
                if os_disk:
                    disks.append({
                        'name': os_disk['name'],
                        'size_gb': os_disk['diskSizeGB'],
                        'type': os_disk['managedDisk']['storageAccountType'],
                        'is_os_disk': True
                    })
                
                # Add data disks
                for data_disk in vm_dict['properties']['storageProfile']['dataDisks']:
                    disks.append({
                        'name': data_disk['name'],
                        'size_gb': data_disk['diskSizeGB'],
                        'type': data_disk['managedDisk']['storageAccountType'],
                        'is_os_disk': False,
                        'lun': data_disk['lun']
                    })
                
                batch_data[vm_info] = {
                    'vm': vm_dict,
                    'nics': nics,
                    'disks': disks
                }
                
                # Cache the data
                save_to_cache(vm_name, resource_group, batch_data[vm_info])
            except Exception as e:
                app.logger.error(f"Error processing VM {vm_info}: {str(e)}")
                continue
            
        return jsonify(batch_data)
        
    except Exception as e:
        app.logger.error(f"Error getting VM batch: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/check-login')
def check_login():
    try:
        credential = AzureCliCredential()
        compute_client = ComputeManagementClient(credential, SUBSCRIPTION_ID)
        # Test the client by listing VMs
        next(compute_client.virtual_machines.list_all(), None)
        return jsonify({"status": "logged_in"})
    except Exception as e:
        app.logger.error(f"Error checking login status: {str(e)}")
        return jsonify({
            "status": "not_logged_in",
            "error": str(e)
        }), 401

@app.route('/api/refresh', methods=['POST'])
def refresh_data():
    try:
        # Refresh cache
        for file in os.listdir(CACHE_DIR):
            os.remove(os.path.join(CACHE_DIR, file))
        
        return jsonify({"status": "complete"})
    except Exception as e:
        app.logger.error(f"Error refreshing data: {str(e)}")
        return jsonify({"error": "Failed to refresh data"}), 500

@app.route('/api/kpi-data')
def get_kpi_data():
    """Get KPI data including cost analysis and resource utilization"""
    try:
        # Get all VMs
        vms = get_vm_list_from_azure()
        
        # Calculate costs and resource usage
        total_cost = 0
        cpu_usage = []
        memory_usage = []
        disk_usage = []
        network_in = []
        network_out = []
        
        for vm in vms:
            # Get VM details including metrics
            vm_details = get_vm_details(vm['name'], vm['resourceGroup'])
            
            # Calculate cost
            monthly_cost = calculate_vm_cost(vm['vmSize'], vm['location'])
            total_cost += monthly_cost
            
            # Get resource metrics (simulated for now)
            cpu = random.uniform(0, 100)
            memory = random.uniform(0, 100)
            disk = random.uniform(0, 100)
            net_in = random.uniform(0, 1000)
            net_out = random.uniform(0, 1000)
            
            cpu_usage.append(cpu)
            memory_usage.append(memory)
            disk_usage.append(disk)
            network_in.append(net_in)
            network_out.append(net_out)
            
        # Prepare response data
        response = {
            'costLabels': [vm['name'] for vm in vms],
            'costData': [calculate_vm_cost(vm['vmSize'], vm['location']) for vm in vms],
            'utilizationLabels': [vm['name'] for vm in vms],
            'cpuData': cpu_usage,
            'memoryData': memory_usage,
            'diskData': disk_usage,
            'networkInData': network_in,
            'networkOutData': network_out,
            'metrics': [
                {
                    'name': vm['name'],
                    'cpu': cpu_usage[i],
                    'memory': memory_usage[i],
                    'disk': disk_usage[i],
                    'networkIn': network_in[i],
                    'networkOut': network_out[i],
                    'cost': calculate_vm_cost(vm['vmSize'], vm['location'])
                }
                for i, vm in enumerate(vms)
            ]
        }
        
        return jsonify(response)
        
    except Exception as e:
        app.logger.error(f"Error getting KPI data: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/instances')
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
        return jsonify({"error": "Failed to fetch instances"}), 500

@app.route('/api/storage-data')
def get_storage_data():
    try:
        # Get storage accounts
        cmd = "az storage account list"
        shell = platform.system() == 'Windows'
        result = subprocess.run(cmd, capture_output=True, text=True, shell=shell)
        if result.returncode == 0:
            storage_accounts = json.loads(result.stdout)
            return jsonify({
                "storage_accounts": storage_accounts
            })
        else:
            app.logger.error(f"Failed to get storage accounts: {result.stderr}")
            return jsonify({"error": "Failed to get storage accounts"}), 500
    except Exception as e:
        app.logger.error(f"Error getting storage data: {str(e)}")
        return jsonify({"error": "Failed to get storage data"}), 500

@app.route('/api/network-data')
def get_network_data():
    try:
        # Get network resources
        cmd = "az network vnet list"
        shell = platform.system() == 'Windows'
        result = subprocess.run(cmd, capture_output=True, text=True, shell=shell)
        if result.returncode == 0:
            vnets = json.loads(result.stdout)
        else:
            app.logger.error(f"Failed to get VNets: {result.stderr}")
            return jsonify({"error": "Failed to get VNets"}), 500
        
        cmd = "az network public-ip list"
        result = subprocess.run(cmd, capture_output=True, text=True, shell=shell)
        if result.returncode == 0:
            public_ips = json.loads(result.stdout)
        else:
            app.logger.error(f"Failed to get public IPs: {result.stderr}")
            return jsonify({"error": "Failed to get public IPs"}), 500
        
        cmd = "az network nsg list"
        result = subprocess.run(cmd, capture_output=True, text=True, shell=shell)
        if result.returncode == 0:
            nsgs = json.loads(result.stdout)
        else:
            app.logger.error(f"Failed to get NSGs: {result.stderr}")
            return jsonify({"error": "Failed to get NSGs"}), 500
        
        return jsonify({
            "vnets": vnets,
            "public_ips": public_ips,
            "nsgs": nsgs
        })
    except Exception as e:
        app.logger.error(f"Error getting network data: {str(e)}")
        return jsonify({"error": "Failed to get network data"}), 500

def get_azure_instances():
    """Get Azure instances with detailed information"""
    try:
        app.logger.info("Fetching Azure instances data...")
        
        credential = AzureCliCredential()
        compute_client = ComputeManagementClient(credential, SUBSCRIPTION_ID)
        
        vms = []
        for vm in compute_client.virtual_machines.list_all():
            vm_instance = compute_client.virtual_machines.instance_view(
                vm.id.split('/')[4],  # resource group
                vm.name
            )
            
            power_state = "Unknown"
            if vm_instance.statuses:
                for status in vm_instance.statuses:
                    if status.code.startswith("PowerState/"):
                        power_state = status.code.split("/")[1]
                        break
            
            vm_dict = vm.as_dict()
            vms.append({
                'name': vm_dict['name'],
                'resourceGroup': vm_dict['id'].split('/')[4],
                'location': vm_dict['location'],
                'size': vm_dict['hardware_profile']['vm_size'],
                'powerState': power_state
            })
        
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

def get_vm_details(vm_name, resource_group):
    """Get detailed VM information including NICs and disks"""
    try:
        # Try to get data from cache first
        cached_data = load_from_cache(vm_name, resource_group)
        if cached_data:
            return cached_data
        
        app.logger.info(f"Getting details for VM {vm_name} in resource group {resource_group}")
        
        credential = AzureCliCredential()
        compute_client = ComputeManagementClient(credential, SUBSCRIPTION_ID)
        
        # Get VM details
        vm = compute_client.virtual_machines.get(resource_group, vm_name, expand='instanceView')
        vm_dict = vm.as_dict()
        
        # Get IP addresses
        nics = []
        for nic in vm_dict['properties']['networkProfile']['networkInterfaces']:
            nic_name = nic['id'].split('/')[-1]
            nic_resource_group = nic['id'].split('/')[4]
            
            # Get NIC details for subnet information
            nic_client = compute_client.network_interfaces.get(nic_resource_group, nic_name)
            nic_dict = nic_client.as_dict()
            
            ip_configs = nic_dict['properties']['ipConfigurations']
            
            for i, ip_config in enumerate(ip_configs):
                subnet_id = ip_config['subnet']['id']
                subnet_parts = subnet_id.split('/') if subnet_id else []
                vnet_name = subnet_parts[-3] if len(subnet_parts) > 3 else 'Not configured'
                subnet_name = subnet_parts[-1] if subnet_parts else 'Not configured'
                
                private_ip = ip_config['properties']['privateIPAddress']
                public_ip = ip_config['properties'].get('publicIPAddress', {}).get('ipAddress') if ip_config['properties'].get('publicIPAddress') else 'Not configured'
                
                nics.append({
                    'name': nic_name,
                    'private_ip': private_ip,
                    'public_ip': public_ip,
                    'subnet': subnet_name,
                    'vnet': vnet_name,
                    'status': 'Configured',
                    'primary': ip_config['properties']['primary']
                })
        
        # Get disk details
        disks = []
        os_disk = vm_dict['properties']['storageProfile']['osDisk']
        if os_disk:
            disks.append({
                'name': os_disk['name'],
                'size_gb': os_disk['diskSizeGB'],
                'type': os_disk['managedDisk']['storageAccountType'],
                'is_os_disk': True
            })
        
        # Add data disks
        for data_disk in vm_dict['properties']['storageProfile']['dataDisks']:
            disks.append({
                'name': data_disk['name'],
                'size_gb': data_disk['diskSizeGB'],
                'type': data_disk['managedDisk']['storageAccountType'],
                'is_os_disk': False,
                'lun': data_disk['lun']
            })
        
        result = {
            'vm': vm_dict,
            'nics': nics,
            'disks': disks
        }
        
        # Save the result to cache
        save_to_cache(vm_name, resource_group, result)
        
        return result
        
    except Exception as e:
        app.logger.error(f"Error getting VM details for {vm_name}: {str(e)}")
        return None

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

if __name__ == '__main__':
    app.run(debug=True)
