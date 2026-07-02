# Utah Water Distribution Migrated Data Comparison Viewer

> **System Status:** Cloud Deployment Ready  
> **Target Platform:** GCP Cloud Run (Serverless)  
> **CI/CD Pipeline:** GitHub Integration $\rightarrow$ Cloud Build Trigger

A lightweight, containerized Python validation engine that serves as an automated verification and visualization bridge between legacy systems and modern cloud telemetry. By fetching data concurrently from the legacy **Utah Division of Water Rights (DVRT)** database and the migrated **HydroServer** ecosystem, it allows engineers, water masters, and data analysts to instantly audit migration integrity.

---

## 📊 Overview & System Architecture

When a request is initiated via query parameters, the application acts as a middleman—extracting, processing, and dynamically aligning datasets with distinct schemas into a unified time-series format without persistent backend storage overhead.


---

## 📋 URL Query Quick Reference

The application dynamically updates its visual embeds, download hyperlinks, and chart metrics depending on the endpoint and routing variables passed to it.

| Environment | Purpose | Target URL Structure |
| :--- | :--- | :--- |
| **Local Environment** | Web Dashboard | `http://localhost:8080/?station_id=9864&start_date=2026-06-01` |
| **Local Environment** | CSV Export | `http://localhost:8080/download.csv?station_id=9864` |
| **GCP Production** | Web Dashboard | `https://[your-cloud-run-url].a.run.app/?station_id=9864` |
| **GCP Production** | CSV Export | `https://[your-cloud-run-url].a.run.app/download.csv?station_id=9864` |

### Parameter Reference
* `station_id` *(Required)*: The designated structural ID matching the Utah Water Rights gauge station.
* `start_date` / `begin_date` *(Optional)*: ISO-formatted limit bounding the earliest sample.
* `end_date` / `stop_date` *(Optional)*: ISO-formatted limit bounding the latest sample.

---
