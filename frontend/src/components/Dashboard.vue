<!-- Dashboard.vue -->
<template>
  <div class="dashboard">
    <div class="row mb-4">
      <div class="col-12">
        <div class="card">
          <div class="card-header d-flex justify-content-between align-items-center">
            <h5 class="mb-0">Azure VM Dashboard</h5>
            <div>
              <button class="btn btn-primary me-2" @click="refreshVMs" :disabled="isLoading">
                <i class="fas fa-sync-alt" :class="{ 'fa-spin': isLoading }"></i>
                Refresh
              </button>
              <button class="btn btn-danger" @click="logout">
                <i class="fas fa-sign-out-alt"></i>
                Logout
              </button>
            </div>
          </div>
          <div class="card-body">
            <div class="row mb-4">
              <div class="col-12">
                <h6>Subscriptions</h6>
                <div class="subscription-list">
                  <div v-for="sub in subscriptions" :key="sub.id" class="form-check">
                    <input
                      type="checkbox"
                      class="form-check-input"
                      :id="sub.id"
                      :value="sub.id"
                      v-model="selectedSubscriptions"
                      @change="loadVMs"
                    >
                    <label class="form-check-label" :for="sub.id">{{ sub.display_name }}</label>
                  </div>
                </div>
              </div>
            </div>

            <!-- Loading Overlay -->
            <div v-if="isLoading" class="text-center py-4">
              <div class="spinner-border text-primary" role="status">
                <span class="visually-hidden">Loading...</span>
              </div>
            </div>

            <!-- Error Alert -->
            <div v-if="error" class="alert alert-danger" role="alert">
              {{ error }}
            </div>

            <!-- VM Table -->
            <div v-if="!isLoading" class="table-responsive">
              <table class="table table-striped table-hover">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Resource Group</th>
                    <th>Location</th>
                    <th>Size</th>
                    <th>Status</th>
                    <th>Network Info</th>
                    <th>OS Type</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-if="vms.length === 0">
                    <td colspan="8" class="text-center">No VMs found in selected subscriptions</td>
                  </tr>
                  <tr v-for="vm in vms" :key="vm.id">
                    <td>{{ vm.name }}</td>
                    <td>{{ vm.resource_group }}</td>
                    <td>{{ vm.location }}</td>
                    <td>{{ vm.vm_size }}</td>
                    <td>
                      <span class="badge" :class="getStatusBadgeClass(vm.status)">
                        {{ vm.status }}
                      </span>
                    </td>
                    <td>
                      <div v-for="(nic, index) in vm.network_info" :key="index">
                        <small>
                          <div>Private IP: {{ nic.private_ip || 'None' }}</div>
                          <div>Public IP: {{ nic.public_ip || 'None' }}</div>
                          <div>Subnet: {{ nic.subnet || 'None' }}</div>
                        </small>
                        <hr v-if="index < vm.network_info.length - 1" class="my-1">
                      </div>
                    </td>
                    <td>{{ vm.os_type }}</td>
                    <td>
                      <div class="btn-group">
                        <button
                          class="btn btn-sm"
                          :class="vm.status === 'running' ? 'btn-warning' : 'btn-success'"
                          @click="startStopVM(vm)"
                          :disabled="isLoading"
                        >
                          {{ vm.status === 'running' ? 'Stop' : 'Start' }}
                        </button>
                        <button
                          class="btn btn-sm btn-info"
                          @click="viewDetails(vm)"
                          :disabled="isLoading"
                        >
                          Details
                        </button>
                      </div>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- VM Details Modal -->
    <div class="modal fade" id="vmDetailsModal" tabindex="-1">
      <div class="modal-dialog modal-lg">
        <div class="modal-content">
          <div class="modal-header">
            <h5 class="modal-title">VM Details</h5>
            <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
          </div>
          <div class="modal-body">
            <pre v-if="selectedVMDetails">{{ JSON.stringify(selectedVMDetails, null, 2) }}</pre>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, onMounted } from 'vue'
import axios from 'axios'
import { Modal } from 'bootstrap'

export default {
  name: 'Dashboard',
  setup() {
    const vms = ref([])
    const subscriptions = ref([])
    const selectedSubscriptions = ref(new Set())
    const isLoading = ref(false)
    const error = ref(null)
    const selectedVMDetails = ref(null)
    let vmDetailsModal = null

    const checkLogin = async () => {
      try {
        const response = await axios.get('/api/check-login')
        if (response.data.status !== 'logged_in') {
          error.value = 'Please login to Azure first using az login'
          return false
        }
        return true
      } catch (err) {
        error.value = 'Error checking login status: ' + (err.response?.data?.error || err.message)
        return false
      }
    }

    const loadSubscriptions = async () => {
      try {
        const response = await axios.get('/api/subscriptions')
        subscriptions.value = response.data
        // Select all subscriptions by default
        selectedSubscriptions.value = new Set(response.data.map(sub => sub.id))
      } catch (err) {
        error.value = 'Error loading subscriptions: ' + (err.response?.data?.error || err.message)
      }
    }

    const loadVMs = async (forceRefresh = false) => {
      if (isLoading.value) return

      isLoading.value = true
      error.value = null

      try {
        const response = await axios.get('/api/vms', {
          params: {
            force_refresh: forceRefresh,
            subscription_ids: Array.from(selectedSubscriptions.value).join(',')
          }
        })
        vms.value = response.data
      } catch (err) {
        error.value = 'Error loading VMs: ' + (err.response?.data?.error || err.message)
      } finally {
        isLoading.value = false
      }
    }

    const refreshVMs = () => loadVMs(true)

    const startStopVM = async (vm) => {
      if (isLoading.value) return

      const action = vm.status === 'running' ? 'stop' : 'start'
      isLoading.value = true
      error.value = null

      try {
        await axios.post(`/api/vm/${action}`, {
          vm_name: vm.name,
          resource_group: vm.resource_group,
          subscription_id: vm.subscription_id
        })
        await loadVMs(true)
      } catch (err) {
        error.value = `Error ${action}ing VM: ` + (err.response?.data?.error || err.message)
      } finally {
        isLoading.value = false
      }
    }

    const viewDetails = async (vm) => {
      if (isLoading.value) return

      isLoading.value = true
      error.value = null

      try {
        const response = await axios.get('/api/vm/details', {
          params: {
            vm_name: vm.name,
            resource_group: vm.resource_group,
            subscription_id: vm.subscription_id
          }
        })
        selectedVMDetails.value = response.data
        vmDetailsModal.show()
      } catch (err) {
        error.value = 'Error fetching VM details: ' + (err.response?.data?.error || err.message)
      } finally {
        isLoading.value = false
      }
    }

    const logout = async () => {
      if (isLoading.value) return

      try {
        await axios.post('/api/logout')
        window.location.reload()
      } catch (err) {
        error.value = 'Error logging out: ' + (err.response?.data?.error || err.message)
      }
    }

    const getStatusBadgeClass = (status) => {
      switch (status.toLowerCase()) {
        case 'running':
          return 'bg-success'
        case 'stopped':
          return 'bg-danger'
        case 'starting':
          return 'bg-warning'
        case 'stopping':
          return 'bg-warning'
        default:
          return 'bg-secondary'
      }
    }

    onMounted(async () => {
      vmDetailsModal = new Modal(document.getElementById('vmDetailsModal'))
      if (await checkLogin()) {
        await loadSubscriptions()
        await loadVMs()
      }
    })

    return {
      vms,
      subscriptions,
      selectedSubscriptions,
      isLoading,
      error,
      selectedVMDetails,
      loadVMs,
      refreshVMs,
      startStopVM,
      viewDetails,
      logout,
      getStatusBadgeClass
    }
  }
}
</script>

<style scoped>
.subscription-list {
  max-height: 200px;
  overflow-y: auto;
  border: 1px solid #dee2e6;
  border-radius: 0.25rem;
  padding: 1rem;
}

.form-check {
  margin-bottom: 0.5rem;
}

.table th {
  white-space: nowrap;
}

.badge {
  font-size: 0.875rem;
}
</style>
