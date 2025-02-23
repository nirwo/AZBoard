from flask import Flask, render_template, jsonify, request
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

app = Flask(__name__)

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
    try:
        app.logger.info("Fetching Azure instances data...")
        
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
        
        # Process the VM data
        instances = []
        for vm in vms:
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
        
        last_refresh_time = datetime.now()
        app.logger.info(f"Successfully fetched data for {len(instances)} instances")
        return instances
    except Exception as e:
        app.logger.error(f"Error in get_azure_instances: {str(e)}")
        return {"error": str(e)}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/instances')
@limiter.limit("30/minute")
def get_instances():
    instances = get_azure_instances()
    refresh_time = humanize.naturaltime(last_refresh_time) if last_refresh_time else 'Never'
    return jsonify({
        'instances': instances,
        'lastRefresh': refresh_time,
        'totalCount': len(instances) if isinstance(instances, list) else 0
    })

@app.route('/api/refresh', methods=['POST'])
@limiter.limit("6/minute")
def refresh_data():
    cache.delete_memoized(get_azure_instances)
    instances = get_azure_instances()
    refresh_time = humanize.naturaltime(last_refresh_time) if last_refresh_time else 'Never'
    return jsonify({
        'instances': instances,
        'lastRefresh': refresh_time,
        'totalCount': len(instances) if isinstance(instances, list) else 0
    })

def init_scheduler():
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=lambda: cache.delete_memoized(get_azure_instances),
                     trigger="interval", minutes=5)
    scheduler.start()

if __name__ == '__main__':
    init_scheduler()
    app.run(debug=True, port=8080, host='0.0.0.0')
