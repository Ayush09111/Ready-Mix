# Integration Routes - ERP and Job Kart integration functionality
from flask import render_template, request, redirect, url_for, session, flash, jsonify
import sqlite3
from datetime import datetime
from app import app, get_db_connection, login_required, log_audit

@app.route('/integration')
@login_required
def integration_home():
    conn = get_db_connection()
    events = conn.execute('''
        SELECT ie.*, o.OrderID, c.CustomerName, jc.JobType
        FROM IntegrationEvents ie
        LEFT JOIN Orders o ON ie.RelatedOrderID = o.OrderID
        LEFT JOIN Customers c ON o.CustomerID = c.CustomerID
        LEFT JOIN JobCards jc ON ie.JobCardID = jc.JobCardID
        ORDER BY ie.EventTime DESC LIMIT 20
    ''').fetchall()
    conn.close()
    return render_template('integration/events.html', events=events)

@app.route('/api/auto_create_jobs', methods=['POST'])
@login_required
def auto_create_jobs():
    """API endpoint to automatically create job cards for confirmed orders"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Find confirmed orders without job cards
    orders_without_jobs = cursor.execute('''
        SELECT o.OrderID, o.CustomerID, o.ProductID, o.Quantity, o.DeliverySite, o.ScheduledDate
        FROM Orders o
        WHERE o.Status = 'Confirmed' 
        AND o.OrderID NOT IN (SELECT DISTINCT RelatedOrderID FROM JobCards WHERE RelatedOrderID IS NOT NULL)
    ''').fetchall()

    created_jobs = 0
    for order in orders_without_jobs:
        # Create delivery job card
        cursor.execute('''
            INSERT INTO JobCards (RelatedOrderID, JobType, Description, AssignedTo, Status, Priority, ScheduledStart, ScheduledEnd)
            VALUES (?, 'Delivery', ?, 5, 'Open', 'Medium', ?, ?)
        ''', (order['OrderID'], 
              f"Deliver {order['Quantity']} units to {order['DeliverySite']}", 
              order['ScheduledDate'] + ' 08:00:00',
              order['ScheduledDate'] + ' 17:00:00'))

        job_id = cursor.lastrowid

        # Log integration event
        cursor.execute('''
            INSERT INTO IntegrationEvents (RelatedOrderID, JobCardID, EventType, EventTime, Details)
            VALUES (?, ?, 'AutoJobCreation', ?, 'Job card automatically created from confirmed order')
        ''', (order['OrderID'], job_id, datetime.now().isoformat()))

        created_jobs += 1

    conn.commit()
    conn.close()

    return jsonify({'success': True, 'created_jobs': created_jobs})

@app.route('/api/sync_inventory', methods=['POST'])
@login_required
def sync_inventory():
    """API endpoint to sync inventory based on completed jobs"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get completed jobs with material usage
    completed_jobs = cursor.execute('''
        SELECT jmu.JobCardID, jmu.MaterialID, SUM(jmu.QuantityUsed) as TotalUsed
        FROM JobMaterialUsage jmu
        JOIN JobCards jc ON jmu.JobCardID = jc.JobCardID
        WHERE jc.Status = 'Completed' 
        AND jmu.JobCardID NOT IN (
            SELECT DISTINCT CAST(Details AS INTEGER) 
            FROM IntegrationEvents 
            WHERE EventType = 'InventorySync' 
            AND Details IS NOT NULL
        )
        GROUP BY jmu.JobCardID, jmu.MaterialID
    ''').fetchall()

    synced_jobs = 0
    for job in completed_jobs:
        # Update inventory
        cursor.execute('''
            UPDATE Inventory 
            SET CurrentStock = CurrentStock - ?, LastUpdated = ?
            WHERE MaterialID = ?
        ''', (job['TotalUsed'], datetime.now().date(), job['MaterialID']))

        # Log integration event
        cursor.execute('''
            INSERT INTO IntegrationEvents (JobCardID, EventType, EventTime, Details)
            VALUES (?, 'InventorySync', ?, ?)
        ''', (job['JobCardID'], datetime.now().isoformat(), str(job['JobCardID'])))

        synced_jobs += 1

    conn.commit()
    conn.close()

    return jsonify({'success': True, 'synced_jobs': synced_jobs})
