<template>
  <nav class="navbar navbar-expand-lg navbar-dark bg-dark mb-4">
    <div class="container-fluid">
      <a class="navbar-brand" href="#">Azure VM Dashboard</a>
      <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#navbarNav">
        <span class="navbar-toggler-icon"></span>
      </button>
      <div class="collapse navbar-collapse" id="navbarNav">
        <ul class="navbar-nav">
          <li class="nav-item">
            <a class="nav-link" :class="{ active: currentView === 'dashboard' }" href="#" @click.prevent="currentView = 'dashboard'">
              <i class="fas fa-server"></i> VMs
            </a>
          </li>
          <li class="nav-item">
            <a class="nav-link" :class="{ active: currentView === 'kpi' }" href="#" @click.prevent="currentView = 'kpi'">
              <i class="fas fa-chart-line"></i> KPI
            </a>
          </li>
        </ul>
      </div>
    </div>
  </nav>

  <div class="container-fluid">
    <component :is="currentComponent"></component>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import Dashboard from './components/Dashboard.vue'
import KPIDashboard from './components/KPIDashboard.vue'

const currentView = ref('dashboard')
    
const currentComponent = computed(() => {
  switch (currentView.value) {
    case 'kpi':
      return KPIDashboard
    default:
      return Dashboard
  }
})

const HelloWorld = () => import('./components/HelloWorld.vue')
</script>

<style>
@import 'bootstrap/dist/css/bootstrap.min.css';
@import '@fortawesome/fontawesome-free/css/all.min.css';

#app {
  min-height: 100vh;
  background-color: #f8f9fa;
}

.navbar-brand {
  font-weight: bold;
}

.nav-link {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
</style>
