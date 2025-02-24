from flask import Flask, jsonify, request, render_template
from azure.identity import AzureCliCredential, DefaultAzureCredential
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.network import NetworkManagementClient
from azure.mgmt.resource import SubscriptionClient
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
import os
import json
import logging
from dotenv import load_dotenv

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///azure_cache.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Database Models
class VMCache(db.Model):
    id = db.Column(db.String(200), primary_key=True)
    subscription_id = db.Column(db.String(100))
    resource_group = db.Column(db.String(100))
    data = db.Column(db.Text)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow)

class SubscriptionCache(db.Model):
    id = db.Column(db.String(100), primary_key=True)
    display_name = db.Column(db.String(200))
    state = db.Column(db.String(50))
    last_updated = db.Column(db.DateTime, default=datetime.utcnow)

# Create tables
with app.app_context():
    db.create_all()

def get_azure_clients():
    try:
        credential = DefaultAzureCredential()
        subscription_client = SubscriptionClient(credential)
        return credential, subscription_client
    except Exception as e:
        logging.error(f"Error getting Azure clients: {str(e)}")
        return None, None

def get_subscriptions():
    try:
        _, subscription_client = get_azure_clients()
        if not subscription_client:
            return []
        
        # Check cache first
        cached_subs = SubscriptionCache.query.all()
        if cached_subs and (datetime.utcnow() - cached_subs[0].last_updated) < timedelta(hours=1):
            return [{"id": sub.id, "display_name": sub.display_name, "state": sub.state} for sub in cached_subs]

        # If cache miss or expired, fetch from Azure
        subscriptions = list(subscription_client.subscriptions.list())
        
        # Update cache
        SubscriptionCache.query.delete()
        for sub in subscriptions:
            cache_entry = SubscriptionCache(
                id=sub.subscription_id,
                display_name=sub.display_name,
                state=sub.state
            )
            db.session.add(cache_entry)
        db.session.commit()
        
        return [{"id": sub.subscription_id, "display_name": sub.display_name, "state": sub.state} 
                for sub in subscriptions]
    except Exception as e:
        logging.error(f"Error fetching subscriptions: {str(e)}")
        return []

def get_vm_data(subscription_id, force_refresh=False):
    try:
        # Check cache first if not forcing refresh
        if not force_refresh:
            cached_vms = VMCache.query.filter_by(subscription_id=subscription_id).all()
            if cached_vms and (datetime.utcnow() - cached_vms[0].last_updated) < timedelta(minutes=5):
                return [json.loads(vm.data) for vm in cached_vms]

        credential, _ = get_azure_clients()
        if not credential:
            return []

        compute_client = ComputeManagementClient(credential, subscription_id)
        network_client = NetworkManagementClient(credential, subscription_id)
        
        vms = []
        for vm in compute_client.virtual_machines.list_all():
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
                    logging.error(f"Error fetching network info: {str(e)}")
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
            
            # Cache the VM data
            cache_entry = VMCache(
                id=vm.id,
                subscription_id=subscription_id,
                resource_group=vm_data['resource_group'],
                data=json.dumps(vm_data),
                last_updated=datetime.utcnow()
            )
            
            db.session.merge(cache_entry)
            vms.append(vm_data)
            
        db.session.commit()
        return vms
    except Exception as e:
        logging.error(f"Error fetching VM data: {str(e)}")
        return []

@app.route('/')
def index():
    subscriptions = get_subscriptions()
    return render_template('index.html', subscriptions=subscriptions)

@app.route('/api/subscriptions')
def list_subscriptions():
    return jsonify(get_subscriptions())

@app.route('/api/check-login')
def check_login():
    try:
        credential, subscription_client = get_azure_clients()
        if not credential or not subscription_client:
            return jsonify({
                "status": "not_logged_in",
                "error": "Failed to get Azure credentials"
            }), 401

        # Test the connection by listing subscriptions
        list(subscription_client.subscriptions.list())
        return jsonify({"status": "logged_in"})
    except Exception as e:
        logging.error(f"Error checking login status: {str(e)}")
        return jsonify({
            "status": "not_logged_in",
            "error": str(e)
        }), 401

@app.route('/api/vms')
def list_vms():
    try:
        force_refresh = request.args.get('force_refresh', 'false').lower() == 'true'
        resource_group = request.args.get('resource_group', '')
        status = request.args.get('status', '')
        vm_size = request.args.get('vm_size', '')
        subscription_id = request.args.get('subscription_id', '')

        if not subscription_id:
            subs = get_subscriptions()
            if subs:
                subscription_id = subs[0]['id']
            else:
                return jsonify([])

        vms = get_vm_data(subscription_id, force_refresh)
        
        # Apply filters
        if resource_group:
            vms = [vm for vm in vms if vm['resource_group'].lower() == resource_group.lower()]
        if status:
            vms = [vm for vm in vms if vm['status'].lower() == status.lower()]
        if vm_size:
            vms = [vm for vm in vms if vm['vm_size'].lower() == vm_size.lower()]
            
        return jsonify(vms)
    except Exception as e:
        logging.error(f"Error in list_vms: {str(e)}")
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
