# Azure Instance Dashboard

A modern web application that displays Azure VM instances information in an interactive dashboard.

## Features

- Display all Azure VM instances in a beautiful, responsive table
- Show instance name, IP address, and subnet information
- Advanced filtering and sorting capabilities
- Export data to various formats (Excel, PDF, Copy)
- Real-time data refresh
- Bootstrap 5 modern UI

## Prerequisites

- Python 3.8+
- Azure CLI installed and configured
- Active Azure subscription

## Installation

### Linux/Mac

1. Clone this repository
2. Install the required packages:
   ```bash
   pip install -r requirements.txt
   ```

3. Make sure you're logged into Azure CLI:
   ```bash
   az login
   ```

### Windows

1. Install Python 3.8 or later from [python.org](https://www.python.org/downloads/)
   - During installation, make sure to check "Add Python to PATH"

2. Install Azure CLI:
   - Download and run the installer from [Microsoft's Azure CLI page](https://docs.microsoft.com/en-us/cli/azure/install-azure-cli-windows)
   - Verify installation by opening Command Prompt and running:
     ```
     az --version
     ```

3. Clone or download this repository

4. Run the dashboard:
   - Double-click `run.bat` in the project directory
   OR
   - Open Command Prompt in the project directory and run:
     ```
     run.bat
     ```

The script will:
- Create a virtual environment
- Install required packages
- Start the dashboard

## Running the Application

1. Start the Flask application:
   ```bash
   python app.py
   ```

2. Open your browser and navigate to `http://localhost:5000`

## Usage

- The dashboard will automatically load all VM instances from your Azure subscription
- Use the search box to filter instances by any field
- Click on column headers to sort the data
- Use the export buttons to download the data in various formats
- Click the "Refresh Data" button to fetch the latest instance information

## Troubleshooting

1. If you see "Python not found":
   - Make sure Python is installed and added to PATH
   - Try running `python --version` in Command Prompt

2. If you see "Azure CLI not found":
   - Make sure Azure CLI is installed
   - Try running `az --version` in Command Prompt

3. If you see "Permission denied" errors:
   - Run Command Prompt as Administrator

4. If packages fail to install:
   - Make sure you have internet connection
   - Try running `pip install -r requirements.txt` manually

## Support

For issues and questions, please create an issue in the repository.
