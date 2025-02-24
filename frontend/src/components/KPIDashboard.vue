<!-- KPIDashboard.vue -->
<template>
  <div class="kpi-dashboard">
    <div class="row mb-4">
      <div class="col-12">
        <div class="card">
          <div class="card-header d-flex justify-content-between align-items-center">
            <h5 class="mb-0">KPI Dashboard</h5>
            <button class="btn btn-primary" @click="refreshData" :disabled="isLoading">
              <i class="fas fa-sync-alt" :class="{ 'fa-spin': isLoading }"></i>
              Refresh
            </button>
          </div>
          <div class="card-body">
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

            <!-- KPI Cards -->
            <div v-if="!isLoading" class="row g-4 mb-4">
              <div class="col-md-3">
                <div class="card bg-primary text-white">
                  <div class="card-body">
                    <h6 class="card-title">Total VMs</h6>
                    <h2 class="card-text">{{ kpiData.total_vms }}</h2>
                  </div>
                </div>
              </div>
              <div class="col-md-3">
                <div class="card bg-success text-white">
                  <div class="card-body">
                    <h6 class="card-title">Running VMs</h6>
                    <h2 class="card-text">{{ kpiData.running_vms }}</h2>
                  </div>
                </div>
              </div>
              <div class="col-md-3">
                <div class="card bg-danger text-white">
                  <div class="card-body">
                    <h6 class="card-title">Stopped VMs</h6>
                    <h2 class="card-text">{{ kpiData.stopped_vms }}</h2>
                  </div>
                </div>
              </div>
              <div class="col-md-3">
                <div class="card bg-warning text-white">
                  <div class="card-body">
                    <h6 class="card-title">Other Status</h6>
                    <h2 class="card-text">{{ kpiData.other_vms }}</h2>
                  </div>
                </div>
              </div>
            </div>

            <!-- Charts -->
            <div v-if="!isLoading" class="row">
              <div class="col-md-6">
                <div class="card">
                  <div class="card-body">
                    <h6 class="card-title">VMs by Region</h6>
                    <DoughnutChart
                      v-if="chartData.regionChart.datasets[0].data.length > 0"
                      :data="chartData.regionChart"
                      :options="chartOptions"
                    />
                    <div v-else class="text-center py-4">
                      No data available
                    </div>
                  </div>
                </div>
              </div>
              <div class="col-md-6">
                <div class="card">
                  <div class="card-body">
                    <h6 class="card-title">VMs by Size</h6>
                    <DoughnutChart
                      v-if="chartData.sizeChart.datasets[0].data.length > 0"
                      :data="chartData.sizeChart"
                      :options="chartOptions"
                    />
                    <div v-else class="text-center py-4">
                      No data available
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, onMounted } from 'vue'
import { DoughnutChart } from 'vue-chartjs'
import { Chart as ChartJS, ArcElement, Tooltip, Legend } from 'chart.js'
import axios from 'axios'

ChartJS.register(ArcElement, Tooltip, Legend)

export default {
  name: 'KPIDashboard',
  components: {
    DoughnutChart
  },
  setup() {
    const isLoading = ref(false)
    const error = ref(null)
    const kpiData = ref({
      total_vms: 0,
      running_vms: 0,
      stopped_vms: 0,
      other_vms: 0
    })

    const chartData = ref({
      regionChart: {
        labels: [],
        datasets: [{
          data: [],
          backgroundColor: []
        }]
      },
      sizeChart: {
        labels: [],
        datasets: [{
          data: [],
          backgroundColor: []
        }]
      }
    })

    const chartOptions = {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: 'right'
        }
      }
    }

    const getRandomColor = () => {
      const letters = '0123456789ABCDEF'
      let color = '#'
      for (let i = 0; i < 6; i++) {
        color += letters[Math.floor(Math.random() * 16)]
      }
      return color
    }

    const loadData = async () => {
      if (isLoading.value) return

      isLoading.value = true
      error.value = null

      try {
        const response = await axios.get('/api/vms')
        const vms = response.data

        // Calculate KPIs
        kpiData.value = {
          total_vms: vms.length,
          running_vms: vms.filter(vm => vm.status.toLowerCase() === 'running').length,
          stopped_vms: vms.filter(vm => vm.status.toLowerCase() === 'stopped').length,
          other_vms: vms.filter(vm => !['running', 'stopped'].includes(vm.status.toLowerCase())).length
        }

        // Process data for charts
        const regionCount = {}
        const sizeCount = {}

        vms.forEach(vm => {
          // Region data
          regionCount[vm.location] = (regionCount[vm.location] || 0) + 1
          
          // Size data
          sizeCount[vm.vm_size] = (sizeCount[vm.vm_size] || 0) + 1
        })

        // Update chart data
        chartData.value.regionChart.labels = Object.keys(regionCount)
        chartData.value.regionChart.datasets[0].data = Object.values(regionCount)
        chartData.value.regionChart.datasets[0].backgroundColor = Object.keys(regionCount).map(() => getRandomColor())

        chartData.value.sizeChart.labels = Object.keys(sizeCount)
        chartData.value.sizeChart.datasets[0].data = Object.values(sizeCount)
        chartData.value.sizeChart.datasets[0].backgroundColor = Object.keys(sizeCount).map(() => getRandomColor())

      } catch (err) {
        error.value = 'Error loading KPI data: ' + (err.response?.data?.error || err.message)
      } finally {
        isLoading.value = false
      }
    }

    const refreshData = () => loadData()

    onMounted(() => {
      loadData()
    })

    return {
      isLoading,
      error,
      kpiData,
      chartData,
      chartOptions,
      refreshData
    }
  }
}
</script>

<style scoped>
.card {
  height: 100%;
}

.chart-container {
  position: relative;
  height: 300px;
}
</style>
