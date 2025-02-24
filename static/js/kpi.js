// KPI page functionality
let kpiData = null;
let metricsTable = null;

function initializeCharts(data) {
    kpiData = data;
    // Store the data properly serialized
    try {
        sessionStorage.setItem('kpiData', JSON.stringify(data));
    } catch (e) {
        console.warn('Failed to store KPI data in session storage:', e);
    }

    // Cost Analysis Chart
    new Chart(document.getElementById('costChart'), {
        type: 'bar',
        data: {
            labels: data.costLabels || [],
            datasets: [{
                label: 'Monthly Cost ($)',
                data: data.costData || [],
                backgroundColor: '#0078D4',
                borderColor: '#0078D4',
                borderWidth: 1
            }]
        },
        options: {
            responsive: true,
            plugins: {
                title: {
                    display: true,
                    text: 'Cost by VM'
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    title: {
                        display: true,
                        text: 'Cost ($)'
                    }
                }
            }
        }
    });

    // Resource Utilization Chart
    new Chart(document.getElementById('utilizationChart'), {
        type: 'bar',
        data: {
            labels: data.utilizationLabels || [],
            datasets: [{
                label: 'CPU Usage (%)',
                data: data.cpuData || [],
                backgroundColor: '#0078D4',
                borderColor: '#0078D4',
                borderWidth: 1
            }, {
                label: 'Memory Usage (%)',
                data: data.memoryData || [],
                backgroundColor: '#50E6FF',
                borderColor: '#50E6FF',
                borderWidth: 1
            }, {
                label: 'Disk Usage (%)',
                data: data.diskData || [],
                backgroundColor: '#28a745',
                borderColor: '#28a745',
                borderWidth: 1
            }]
        },
        options: {
            responsive: true,
            plugins: {
                title: {
                    display: true,
                    text: 'Resource Utilization by VM'
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    max: 100,
                    title: {
                        display: true,
                        text: 'Usage (%)'
                    }
                }
            }
        }
    });
}

function formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function updateMetricsTable(data) {
    if (metricsTable) {
        metricsTable.destroy();
    }

    const tbody = $('#metricsTable tbody');
    tbody.empty();

    if (!data.metrics) {
        showError('No metrics data available');
        return;
    }

    data.metrics.forEach(metric => {
        const row = `
            <tr>
                <td>${metric.name || 'N/A'}</td>
                <td>${(metric.cpu || 0).toFixed(1)}%</td>
                <td>${(metric.memory || 0).toFixed(1)}%</td>
                <td>${(metric.disk || 0).toFixed(1)}%</td>
                <td>${formatBytes(metric.networkIn || 0)}/s</td>
                <td>${formatBytes(metric.networkOut || 0)}/s</td>
                <td>$${(metric.cost || 0).toFixed(2)}</td>
            </tr>
        `;
        tbody.append(row);
    });

    // Initialize DataTable with custom settings
    metricsTable = $('#metricsTable').DataTable({
        pageLength: 10,
        lengthMenu: [[10, 25, 50, -1], [10, 25, 50, "All"]],
        responsive: true,
        dom: '<"row"<"col-sm-12 col-md-6"l><"col-sm-12 col-md-6"f>>' +
             '<"row"<"col-sm-12"tr>>' +
             '<"row"<"col-sm-12 col-md-5"i><"col-sm-12 col-md-7"p>>',
        language: {
            search: "Search VMs:",
            lengthMenu: "Show _MENU_ VMs per page",
            info: "Showing _START_ to _END_ of _TOTAL_ VMs"
        }
    });
}

function showError(message) {
    const alertDiv = $('<div>')
        .addClass('alert alert-danger alert-dismissible fade show')
        .attr('role', 'alert')
        .html(`
            ${message}
            <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
        `);
    
    $('#alertContainer').empty().append(alertDiv);
    
    // Auto-dismiss after 5 seconds
    setTimeout(() => {
        alertDiv.alert('close');
    }, 5000);
}

function showLoading() {
    $('#loadingOverlay').removeClass('d-none');
}

function hideLoading() {
    $('#loadingOverlay').addClass('d-none');
}

function loadKPIData() {
    showLoading();
    
    // Try to load cached data first
    try {
        const cachedData = sessionStorage.getItem('kpiData');
        if (cachedData) {
            const data = JSON.parse(cachedData);
            initializeCharts(data);
            updateMetricsTable(data);
            hideLoading();
        }
    } catch (e) {
        console.warn('Failed to load cached KPI data:', e);
    }
    
    // Always fetch fresh data
    $.ajax({
        url: '/api/kpi-data',
        method: 'GET',
        success: function(data) {
            if (data.error) {
                showError(data.error);
                return;
            }
            initializeCharts(data);
            updateMetricsTable(data);
        },
        error: function(xhr, status, error) {
            console.error('Error loading KPI data:', error);
            showError('Failed to load KPI data. Please try again later.');
        },
        complete: function() {
            hideLoading();
        }
    });
}

// Initialize KPI page
$(document).ready(function() {
    loadKPIData();
    
    // Refresh every 5 minutes
    setInterval(loadKPIData, 5 * 60 * 1000);
});
