// Common functionality shared across pages
let loadingTimer;
let currentProgress = 0;

function showToast(message, type = 'success') {
    const toastHtml = `
        <div class="toast" role="alert">
            <div class="toast-header bg-${type} text-white">
                <strong class="me-auto">${type.charAt(0).toUpperCase() + type.slice(1)}</strong>
                <button type="button" class="btn-close btn-close-white" data-bs-dismiss="toast"></button>
            </div>
            <div class="toast-body">${message}</div>
        </div>
    `;
    
    const $toast = $(toastHtml);
    $('.toast-container').append($toast);
    const toast = new bootstrap.Toast($toast[0]);
    toast.show();
    
    $toast.on('hidden.bs.toast', function() {
        $(this).remove();
    });
}

function updateLoadingProgress(progress) {
    currentProgress = progress;
    $('.progress-bar').css('width', `${progress}%`);
}

function startLoadingAnimation() {
    $('.loading-overlay').show();
    currentProgress = 0;
    updateLoadingProgress(0);
    
    loadingTimer = setInterval(() => {
        if (currentProgress < 90) {
            updateLoadingProgress(currentProgress + Math.random() * 10);
        }
    }, 1000);
}

function stopLoadingAnimation() {
    updateLoadingProgress(100);
    clearInterval(loadingTimer);
    setTimeout(() => {
        $('.loading-overlay').hide();
        updateLoadingProgress(0);
    }, 500);
}

function showLoginOverlay(loginUrl) {
    $('.login-overlay').show();
    $('#azureLoginBtn').attr('href', loginUrl);
}

function hideLoginOverlay() {
    $('.login-overlay').hide();
}

function checkAzureLogin() {
    return $.get('/api/check-login')
        .then(response => {
            if (response.status === 'not_logged_in' && response.login_url) {
                showLoginOverlay(response.login_url);
                return false;
            } else if (response.status === 'logged_in') {
                hideLoginOverlay();
                return true;
            }
        })
        .catch(error => {
            console.error('Error checking login status:', error);
            showToast('Failed to check Azure login status', 'danger');
            return false;
        });
}

// Initialize common functionality
$(document).ready(function() {
    // Check Azure login status when page loads
    checkAzureLogin();
    
    // Add click handler for Azure login button
    $('#azureLoginBtn').click(function() {
        const loginUrl = $(this).attr('href');
        if (loginUrl && loginUrl !== '#') {
            window.open(loginUrl, '_blank');
            // Start polling for login status
            const pollInterval = setInterval(function() {
                $.get('/api/check-login', function(response) {
                    if (response.status === 'logged_in') {
                        clearInterval(pollInterval);
                        hideLoginOverlay();
                        location.reload();
                        showToast('Successfully logged in to Azure', 'success');
                    }
                });
            }, 5000); // Check every 5 seconds
        }
    });

    // User profile dropdown
    $('#userProfileBtn').click(function() {
        checkAzureLogin();
    });
});
