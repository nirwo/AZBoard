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

1. Clone this repository
2. Install the required packages:
   ```bash
   pip install -r requirements.txt
   ```

3. Make sure you're logged into Azure CLI:
   ```bash
   az login
   ```

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
