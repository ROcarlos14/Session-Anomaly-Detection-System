# Session Anomaly Detection System

A real time, multi engine browser threat detection system using machine learning and behavioral analysis.

## Overview
This project is an advanced cybersecurity solution designed to protect enterprise environments from modern web threats. It captures granular telemetry directly from the user browser and streams it into a robust detection pipeline. The system identifies attacks like Cross Site Scripting, DOM injection, and data exfiltration without requiring server side configuration.

## System Architecture
The platform consists of five distinct components working together perfectly:

* **Browser Extension**: A Manifest V3 Chrome extension that acts as the primary sensor. It monitors network requests, tab navigation, and structural DOM mutations.
* **C Plus Plus Real Time Engine**: A deterministic detection service that uses sliding time windows to catch immediate threshold violations. It is incredibly fast and precise.
* **Python Machine Learning Engine**: A behavioral detection service utilizing Isolation Forests and LSTM Autoencoders. It identifies subtle anomalies that do not trigger hard rules.
* **Nim Orchestrator**: The central fusion component. It listens to both detection engines via Redis Streams and combines their scores to produce a single actionable threat severity level.
* **Ruby on Rails Dashboard**: The Security Operations Center interface. It provides security analysts with instant WebSockets alerts and detailed forensic views.

## Installation and Setup

### Prerequisites
* Docker and Docker Compose installed on your system
* Google Chrome or Chromium based browser

### Deployment Steps
1. Clone the repository to your local machine.
2. Open a terminal and navigate to the project root directory.
3. Start the entire microservices cluster by running docker compose up.
4. Open your browser and navigate to the Rails dashboard at localhost port 3000.
5. Load the Chrome extension manually by enabling Developer Mode and selecting the unpacked extension folder.

## Technologies Used
* **Frontend**: HTML, CSS, JavaScript
* **Backend Framework**: Ruby on Rails
* **Machine Learning**: Python, TensorFlow, Scikit Learn
* **High Performance Services**: C Plus Plus, Nim
* **Data Streaming and Storage**: Redis, PostgreSQL
