from flask import Flask, jsonify, request, render_template, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta, timezone
import os
import json
import logging
from azure.identity import DefaultAzureCredential, AzureCliCredential, ClientSecretCredential
from azure.mgmt.resource import SubscriptionClient
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.network import NetworkManagementClient
from flask_cors import CORS
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configure CORS
CORS(app, resources={
    r"/api/*": {
        "origins": ["http://localhost:5173"],
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization", "X-Requested-With"],
        "supports_credentials": True
    }
})

@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', 'http://localhost:5173')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization,X-Requested-With')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    response.headers.add('Access-Control-Allow-Credentials', 'true')
    return response

# Database configuration from environment
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('SQLALCHEMY_DATABASE_URI', 'sqlite:///azure_cache.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'default-dev-key')

# Cache duration configuration
VM_CACHE_DURATION = timedelta(minutes=int(os.getenv('VM_CACHE_DURATION_MINUTES', 5)))
SUBSCRIPTION_CACHE_DURATION = timedelta(hours=int(os.getenv('SUBSCRIPTION_CACHE_DURATION_HOURS', 1)))

db = SQLAlchemy(app)

# Database Models
class VMCache(db.Model):
    id = db.Column(db.String(200), primary_key=True)
    subscription_id = db.Column(db.String(100))
    resource_group = db.Column(db.String(100))
    data = db.Column(db.Text)
    last_updated = db.Column(db.DateTime(timezone=True), default=datetime.now(timezone.utc))

class SubscriptionCache(db.Model):
    id = db.Column(db.String(100), primary_key=True)
    display_name = db.Column(db.String(200))
    state = db.Column(db.String(50))
    last_updated = db.Column(db.DateTime(timezone=True), default=datetime.now(timezone.utc))

# Create tables
with app.app_context():
    db.create_all()

def get_azure_credential():
    try:
        # Try DefaultAzureCredential first
        credential = DefaultAzureCredential()
        # Test the credential
        token = credential.get_token("https://management.azure.com/.default")
        if token:
            return credential
    except Exception as e:
        app.logger.error(f"Error getting DefaultAzureCredential: {str(e)}")
        pass

    try:
        # Try AzureCliCredential as fallback
        credential = AzureCliCredential()
        # Test the credential
        token = credential.get_token("https://management.azure.com/.default")
        if token:
            return credential
    except Exception as e:
        app.logger.error(f"Error getting AzureCliCredential: {str(e)}")
        pass

    return None

def get_subscriptions():
    try:
        app.logger.info("Fetching subscriptions...")
        credential = get_azure_credential()
        if not credential:
            app.logger.error("No valid Azure credential found")
            return []
            
        subscription_client = SubscriptionClient(credential)
        
        subscriptions = []
        for sub in subscription_client.subscriptions.list():
            app.logger.info(f"Found subscription: {sub.display_name} ({sub.subscription_id})")
            subscriptions.append({
                'id': sub.subscription_id,
                'display_name': sub.display_name
            })
            
        if not subscriptions:
            app.logger.warning("No subscriptions found")
            
        return subscriptions
    except Exception as e:
        app.logger.error(f"Error fetching subscriptions: {str(e)}")
        return []

def get_azure_clients():
    try:
        credential = get_azure_credential()
        if not credential:
            raise Exception("Failed to get Azure credentials")
            
        subscription_client = SubscriptionClient(credential)
        return credential, subscription_client
    except Exception as e:
        app.logger.error(f"Error getting Azure clients: {str(e)}")
        return None, None

def is_cache_expired(last_updated, cache_duration):
    return datetime.now(timezone.utc) - last_updated > cache_duration

def fetch_and_cache_vms(compute_client, subscription_id):
    vms = []
    try:
        for vm in compute_client.virtual_machines.list_all():
            try:
                vm_instance = compute_client.virtual_machines.get(
                    vm.id.split('/')[4],
                    vm.name,
                    expand='instanceView'
                )
                
                # Get network interface info
                network_info = []
                for nic_ref in vm.network_profile.network_interfaces:
                    nic_name = nic_ref.id.split('/')[-1]
                    resource_group = nic_ref.id.split('/')[4]
                    try:
                        nic = NetworkManagementClient(get_azure_credential(), subscription_id).network_interfaces.get(resource_group, nic_name)
                        for ip_config in nic.ip_configurations:
                            network_info.append({
                                'private_ip': ip_config.private_ip_address,
                                'public_ip': ip_config.public_ip_address.ip_address if ip_config.public_ip_address else None,
                                'subnet': ip_config.subnet.id.split('/')[-1] if ip_config.subnet else None
                            })
                    except Exception as e:
                        app.logger.error(f"Error fetching network info: {str(e)}")
                        network_info.append({'error': 'Failed to fetch network information'})

                vm_data = {
                    'id': vm.id,
                    'name': vm.name,
                    'resource_group': vm.id.split('/')[4],
                    'location': vm.location,
                    'vm_size': vm.hardware_profile.vm_size,
                    'os_type': vm.storage_profile.os_disk.os_type,
                    'status': next((status.display_status for status in vm_instance.instance_view.statuses if status.code.startswith('PowerState/')), 'unknown'),
                    'network_info': network_info,
                    'subscription_id': subscription_id
                }
                
                # Cache each VM separately
                cache_entry = VMCache(
                    id=vm.id,
                    subscription_id=subscription_id,
                    resource_group=vm_data['resource_group'],
                    data=json.dumps(vm_data),
                    last_updated=datetime.now(timezone.utc)
                )
                
                db.session.merge(cache_entry)
                vms.append(vm_data)
            except Exception as e:
                app.logger.error(f"Error processing VM {vm.name}: {str(e)}")
                continue
                
        db.session.commit()
    except Exception as e:
        app.logger.error(f"Error fetching VMs from Azure: {str(e)}")
        db.session.rollback()
        
    return vms

@app.route('/')
def index():
    try:
        subscriptions = get_subscriptions()
        if not subscriptions:
            app.logger.warning("No subscriptions found")
        return render_template('index.html', subscriptions=subscriptions)
    except Exception as e:
        app.logger.error(f"Error in index route: {str(e)}")
        return render_template('index.html', subscriptions=[])

@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory('../frontend/dist', path)

@app.route('/api/subscriptions')
def list_subscriptions():
    return jsonify(get_subscriptions())

@app.route('/api/check-login')
def check_login():
    try:
        # Try to get subscriptions as a login test
        subscriptions = get_subscriptions()
        if subscriptions:
            return jsonify({'status': 'logged_in'})
        else:
            return jsonify({'status': 'not_logged_in', 'message': 'No subscriptions found. Please login to Azure.'}), 401
    except Exception as e:
        app.logger.error(f"Error checking login: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/vms')
def get_vms():
    try:
        app.logger.info("Fetching VMs...")
        force_refresh = request.args.get('force_refresh', 'false').lower() == 'true'
        subscription_ids = request.args.get('subscription_ids', '').split(',')
        resource_group = request.args.get('resource_group')
        status = request.args.get('status')
        vm_size = request.args.get('vm_size')

        # If no subscriptions selected, try to get all subscriptions
        if not subscription_ids or not subscription_ids[0]:
            app.logger.info("No subscriptions selected, getting all subscriptions")
            subscriptions = get_subscriptions()
            if subscriptions:
                subscription_ids = [sub['id'] for sub in subscriptions]
            else:
                app.logger.warning("No subscriptions found")
                return jsonify([])

        app.logger.info(f"Processing {len(subscription_ids)} subscriptions")
        all_vms = []
        for subscription_id in subscription_ids:
            subscription_id = subscription_id.strip()
            if not subscription_id:
                continue

            try:
                app.logger.info(f"Processing subscription: {subscription_id}")
                # Get VMs for this subscription
                credential = get_azure_credential()
                if not credential:
                    app.logger.error("No valid Azure credential found")
                    continue
                    
                compute_client = ComputeManagementClient(credential, subscription_id)
                network_client = NetworkManagementClient(credential, subscription_id)
                
                # Get VMs from cache or Azure
                if not force_refresh:
                    cached_vms = VMCache.query.filter_by(subscription_id=subscription_id).all()
                    if cached_vms and not is_cache_expired(cached_vms[0].last_updated, VM_CACHE_DURATION):
                        app.logger.info(f"Using cached VMs for subscription {subscription_id}")
                        # Each cached VM is a separate record
                        vms_data = []
                        for cached_vm in cached_vms:
                            try:
                                vm_data = json.loads(cached_vm.data)
                                vms_data.append(vm_data)
                            except json.JSONDecodeError as e:
                                app.logger.error(f"Error decoding cached VM data: {str(e)}")
                                continue
                    else:
                        app.logger.info(f"Cache miss or expired for subscription {subscription_id}")
                        vms_data = []
                        for vm in compute_client.virtual_machines.list_all():
                            try:
                                vm_instance = compute_client.virtual_machines.get(
                                    vm.id.split('/')[4],
                                    vm.name,
                                    expand='instanceView'
                                )
                                
                                # Get network interface info
                                network_info = []
                                for nic_ref in vm.network_profile.network_interfaces:
                                    nic_name = nic_ref.id.split('/')[-1]
                                    resource_group = nic_ref.id.split('/')[4]
                                    try:
                                        nic = network_client.network_interfaces.get(resource_group, nic_name)
                                        for ip_config in nic.ip_configurations:
                                            network_info.append({
                                                'private_ip': ip_config.private_ip_address,
                                                'public_ip': ip_config.public_ip_address.ip_address if ip_config.public_ip_address else None,
                                                'subnet': ip_config.subnet.id.split('/')[-1] if ip_config.subnet else None
                                            })
                                    except Exception as e:
                                        app.logger.error(f"Error fetching network info: {str(e)}")
                                        network_info.append({'error': 'Failed to fetch network information'})

                                vm_data = {
                                    'id': vm.id,
                                    'name': vm.name,
                                    'resource_group': vm.id.split('/')[4],
                                    'location': vm.location,
                                    'vm_size': vm.hardware_profile.vm_size,
                                    'os_type': vm.storage_profile.os_disk.os_type,
                                    'status': next((status.display_status for status in vm_instance.instance_view.statuses if status.code.startswith('PowerState/')), 'unknown'),
                                    'network_info': network_info,
                                    'subscription_id': subscription_id
                                }
                                
                                # Cache each VM separately
                                cache_entry = VMCache(
                                    id=vm.id,
                                    subscription_id=subscription_id,
                                    resource_group=vm_data['resource_group'],
                                    data=json.dumps(vm_data),
                                    last_updated=datetime.now(timezone.utc)
                                )
                                
                                db.session.merge(cache_entry)
                                vms_data.append(vm_data)
                                app.logger.info(f"Processed VM: {vm.name}")
                            except Exception as e:
                                app.logger.error(f"Error processing VM {vm.name}: {str(e)}")
                                continue
                        
                        db.session.commit()
                else:
                    app.logger.info(f"Force refresh requested for subscription {subscription_id}")
                    vms_data = []
                    for vm in compute_client.virtual_machines.list_all():
                        try:
                            vm_instance = compute_client.virtual_machines.get(
                                vm.id.split('/')[4],
                                vm.name,
                                expand='instanceView'
                            )
                            
                            # Get network interface info
                            network_info = []
                            for nic_ref in vm.network_profile.network_interfaces:
                                nic_name = nic_ref.id.split('/')[-1]
                                resource_group = nic_ref.id.split('/')[4]
                                try:
                                    nic = network_client.network_interfaces.get(resource_group, nic_name)
                                    for ip_config in nic.ip_configurations:
                                        network_info.append({
                                            'private_ip': ip_config.private_ip_address,
                                            'public_ip': ip_config.public_ip_address.ip_address if ip_config.public_ip_address else None,
                                            'subnet': ip_config.subnet.id.split('/')[-1] if ip_config.subnet else None
                                        })
                                except Exception as e:
                                    app.logger.error(f"Error fetching network info: {str(e)}")
                                    network_info.append({'error': 'Failed to fetch network information'})

                            vm_data = {
                                'id': vm.id,
                                'name': vm.name,
                                'resource_group': vm.id.split('/')[4],
                                'location': vm.location,
                                'vm_size': vm.hardware_profile.vm_size,
                                'os_type': vm.storage_profile.os_disk.os_type,
                                'status': next((status.display_status for status in vm_instance.instance_view.statuses if status.code.startswith('PowerState/')), 'unknown'),
                                'network_info': network_info,
                                'subscription_id': subscription_id
                            }
                            
                            # Cache each VM separately
                            cache_entry = VMCache(
                                id=vm.id,
                                subscription_id=subscription_id,
                                resource_group=vm_data['resource_group'],
                                data=json.dumps(vm_data),
                                last_updated=datetime.now(timezone.utc)
                            )
                            
                            db.session.merge(cache_entry)
                            vms_data.append(vm_data)
                            app.logger.info(f"Processed VM: {vm.name}")
                        except Exception as e:
                            app.logger.error(f"Error processing VM {vm.name}: {str(e)}")
                            continue
                    
                    db.session.commit()

                # Apply filters
                filtered_vms = []
                for vm in vms_data:
                    if resource_group and vm['resource_group'].lower() != resource_group.lower():
                        continue
                    if status and vm['status'].lower() != status.lower():
                        continue
                    if vm_size and vm['vm_size'].lower() != vm_size.lower():
                        continue
                    filtered_vms.append(vm)

                all_vms.extend(filtered_vms)
                app.logger.info(f"Found {len(filtered_vms)} VMs in subscription {subscription_id}")
            except Exception as e:
                app.logger.error(f"Error processing subscription {subscription_id}: {str(e)}")
                continue

        app.logger.info(f"Returning {len(all_vms)} total VMs")
        return jsonify(all_vms)

    except Exception as e:
        app.logger.error(f"Error fetching VMs: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/kpi')
def kpi():
    return render_template('kpi.html')

@app.route('/api/kpi')
def get_kpi():
    try:
        all_vms = []
        for sub in get_subscriptions():
            try:
                credential = get_azure_credential()
                compute_client = ComputeManagementClient(credential, sub['id'])
                vms = fetch_and_cache_vms(compute_client, sub['id'])
                all_vms.extend(vms)
            except Exception as e:
                app.logger.error(f"Error fetching VMs for subscription {sub['id']}: {str(e)}")
                continue

        if not all_vms:
            return jsonify({
                'total_vms': 0,
                'running_vms': 0,
                'stopped_vms': 0,
                'total_cost': 0,
                'regions': {},
                'vm_sizes': {}
            })

        # Calculate KPIs
        total_vms = len(all_vms)
        running_vms = sum(1 for vm in all_vms if vm['status'].lower() == 'running')
        stopped_vms = sum(1 for vm in all_vms if vm['status'].lower() in ['stopped', 'deallocated'])
        
        # Region distribution
        regions = {}
        for vm in all_vms:
            region = vm['location']
            regions[region] = regions.get(region, 0) + 1
            
        # VM size distribution
        vm_sizes = {}
        for vm in all_vms:
            size = vm['vm_size']
            vm_sizes[size] = vm_sizes.get(size, 0) + 1

        return jsonify({
            'total_vms': total_vms,
            'running_vms': running_vms,
            'stopped_vms': stopped_vms,
            'regions': regions,
            'vm_sizes': vm_sizes
        })

    except Exception as e:
        app.logger.error(f"Error calculating KPIs: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/logout', methods=['POST'])
def logout():
    try:
        # Clear the database cache
        db.session.query(VMCache).delete()
        db.session.query(SubscriptionCache).delete()
        db.session.commit()
        
        # Run Azure CLI logout
        os.system('az logout')
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
