// Get the Ingress base path (injected from server via data attribute)
const BASE_PATH = document.body.dataset.ingressPath || '';

// Auto-load devices on page load
document.addEventListener('DOMContentLoaded', () => {
  loadDevices();
  setupRegistrationForm();
});

function setupRegistrationForm() {
  document.getElementById('registerForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const code = document.getElementById('entryCode').value.toUpperCase().trim();
    const resultDiv = document.getElementById('registerResult');

    if (code.length !== 7) {
      resultDiv.innerHTML = '<p class="result-error">Entry code must be 7 characters</p>';
      return;
    }

    resultDiv.innerHTML = '<p>Registering device...</p>';

    try {
      const response = await fetch(BASE_PATH + '/api/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          code: code,
          userId: 'homeassistant'
        })
      });

      const result = await response.json();

      if (result.success) {
        resultDiv.innerHTML = '<p class="result-success">✓ ' + result.message + '</p>';
        document.getElementById('entryCode').value = '';
        setTimeout(loadDevices, 1000);
      } else {
        resultDiv.innerHTML = '<p class="result-error">✗ ' + result.message + '</p>';
      }
    } catch (error) {
      resultDiv.innerHTML = '<p class="result-error">Error: ' + error.message + '</p>';
    }
  });
}

async function loadDevices() {
  const deviceList = document.getElementById('deviceList');
  try {
    const response = await fetch(BASE_PATH + '/api/registered-devices?userId=homeassistant');
    const devices = await response.json();

    if (devices.length === 0) {
      deviceList.innerHTML = '<p><em>No devices registered yet</em></p>';
    } else {
      deviceList.innerHTML = '<ul class="device-list">' +
        devices.map(d => {
          const date = new Date(d.createdAt);
          return '<li class="device-item">' +
            '<span><strong>' + d.serial + '</strong> - Registered ' + date.toLocaleString() + '</span>' +
            '<button onclick="deleteDevice(\'' + d.serial + '\')" class="btn btn-danger" title="Delete device">' +
            '<i class="mdi mdi-delete"></i></button>' +
            '</li>';
        }).join('') +
        '</ul>';
    }
  } catch (error) {
    deviceList.innerHTML = '<p class="result-error">Error loading devices</p>';
  }
}

async function deleteDevice(serial) {
  if (!confirm('Are you sure you want to delete device ' + serial + '?')) {
    return;
  }

  try {
    const response = await fetch(
      BASE_PATH + '/api/registered-devices/' + encodeURIComponent(serial) + '?userId=homeassistant',
      { method: 'DELETE' }
    );

    const result = await response.json();

    if (result.success) {
      loadDevices();
    } else {
      alert('Failed to delete device: ' + result.message);
    }
  } catch (error) {
    alert('Error deleting device: ' + error.message);
  }
}
