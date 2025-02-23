// KPI page functionality
function initializeCharts(data) {
    // Cost Analysis Chart
    new Chart(document.getElementById('costChart'), {
        type: 'line',
        data: {
            labels: data.costLabels,
            datasets: [{
                label: 'Estimated Cost',
                data: data.costData,
                borderColor: '#0078D4',
                tension: 0.1
            }]
        },
        options: {
            responsive: true,
            plugins: {
                title: {
                    display: true,
                    text: 'Monthly Cost Trend'
                }
            }
        }
    });

    // Resource Utilization Chart
    new Chart(document.getElementById('utilizationChart'), {
        type: 'bar',
        data: {
            labels: data.utilizationLabels,
            datasets: [{
                label: 'CPU Usage',
                data: data.cpuData,
                backgroundColor: '#0078D4'
            }, {
                label: 'Memory Usage',
                data: data.memoryData,
                backgroundColor: '#50E6FF'
            }]
        },
        options: {
            responsive: true,
            plugins: {
                title: {
                    display: true,
                    text: 'Resource Utilization'
                }
            }
        }
    });

    // Resource Group Distribution Chart
    new Chart(document.getElementById('resourceGroupChart'), {
        type: 'doughnut',
        data: {
            labels: data.resourceGroups,
            datasets: [{
                data: data.resourceGroupCounts,
                backgroundColor: [
                    '#0078D4',
                    '#50E6FF',
                    '#28a745',
                    '#dc3545',
                    '#ffc107'
                ]
            }]
        },
        options: {
            responsive: true,
            plugins: {
                title: {
                    display: true,
                    text: 'VMs by Resource Group'
                }
            }
        }
    });

    // VM Size Distribution Chart
    new Chart(document.getElementById('vmSizeChart'), {
        type: 'pie',
        data: {
            labels: data.vmSizes,
            datasets: [{
                data: data.vmSizeCounts,
                backgroundColor: [
                    '#0078D4',
                    '#50E6FF',
                    '#28a745',
                    '#dc3545',
                    '#ffc107'
                ]
            }]
        },
        options: {
            responsive: true,
            plugins: {
                title: {
                    display: true,
                    text: 'VMs by Size'
                }
            }
        }
    });

    // Status Distribution Chart
    new Chart(document.getElementById('statusChart'), {
        type: 'bar',
        data: {
            labels: data.statusLabels,
            datasets: [{
                label: 'VM Count',
                data: data.statusCounts,
                backgroundColor: [
                    '#28a745',
                    '#dc3545',
                    '#ffc107'
                ]
            }]
        },
        options: {
            responsive: true,
            plugins: {
                title: {
                    display: true,
                    text: 'VMs by Status'
                }
            }
        }
    });
}

function updateMetricsTable(data) {
    const tbody = $('#metricsTable tbody');
    tbody.empty();

    data.metrics.forEach(metric => {
        const row = `
            <tr>
                <td>${metric.name}</td>
                <td>${metric.current}</td>
                <td>${metric.target}</td>
                <td>
                    <span class="badge bg-${metric.status === 'good' ? 'success' : 'warning'}">
                        ${metric.status}
                    </span>
                </td>
                <td>
                    <i class="fas fa-${metric.trend === 'up' ? 'arrow-up text-success' : 'arrow-down text-danger'}"></i>
                    ${metric.trendValue}%
                </td>
            </tr>
        `;
        tbody.append(row);
    });
}

function loadKPIData() {
    startLoadingAnimation();
    
    $.get('/api/kpi-data')
        .then(data => {
            initializeCharts(data);
            updateMetricsTable(data);
        })
        .catch(error => {
            console.error('Error loading KPI data:', error);
            showToast('Failed to load KPI data', 'danger');
        })
        .finally(() => {
            stopLoadingAnimation();
        });
}

// Initialize KPI page
$(document).ready(function() {
    if (checkAzureLogin()) {
        loadKPIData();
    }
});
