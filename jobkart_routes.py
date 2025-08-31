# Job Kart Routes - Job card management functionality
from flask import render_template, request, redirect, url_for, session, flash, jsonify
import sqlite3
from datetime import datetime
from app import app, get_db_connection, login_required, log_audit

@app.route('/jobkart')
@login_required
def jobkart_home():
    return render_template('jobkart/index.html')

@app.route('/jobkart/jobs')
@login_required
def jobkart_jobs():
    conn = get_db_connection()
    jobs = conn.execute('''
        SELECT jc.JobCardID, jc.RelatedOrderID, jc.JobType, jc.Description, jc.Status, 
               jc.Priority, jc.ScheduledStart, jc.ScheduledEnd, e.Name as AssignedTo
        FROM JobCards jc
        LEFT JOIN Employees e ON jc.AssignedTo = e.EmployeeID
        ORDER BY jc.ScheduledStart DESC
    ''').fetchall()
    conn.close()
    return render_template('jobkart/jobs.html', jobs=jobs)

@app.route('/jobkart/jobs/new', methods=['GET', 'POST'])
@login_required
def jobkart_new_job():
    if request.method == 'POST':
        job_type = request.form['job_type']
        description = request.form['description']
        assigned_to = request.form['assigned_to']
        priority = request.form['priority']
        scheduled_start = request.form['scheduled_start']
        scheduled_end = request.form['scheduled_end']
        related_order = request.form.get('related_order') or None

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO JobCards (RelatedOrderID, JobType, Description, AssignedTo, Status, Priority, ScheduledStart, ScheduledEnd)
            VALUES (?, ?, ?, ?, 'Open', ?, ?, ?)
        ''', (related_order, job_type, description, assigned_to, priority, scheduled_start, scheduled_end))

        job_id = cursor.lastrowid
        log_audit('JobCard', job_id, 'Create', session['user_id'], f"New job card created: {job_type}")

        conn.commit()
        conn.close()

        flash('Job card created successfully!', 'success')
        return redirect(url_for('jobkart_jobs'))

    conn = get_db_connection()
    employees = conn.execute('SELECT * FROM Employees WHERE Status = "Active" ORDER BY Name').fetchall()
    orders = conn.execute('''
        SELECT o.OrderID, c.CustomerName, p.ProductName 
        FROM Orders o 
        JOIN Customers c ON o.CustomerID = c.CustomerID 
        JOIN Products p ON o.ProductID = p.ProductID 
        WHERE o.Status IN ("Confirmed", "In Production") 
        ORDER BY o.OrderID DESC
    ''').fetchall()
    conn.close()

    return render_template('jobkart/new_job.html', employees=employees, orders=orders)

@app.route('/jobkart/jobs/<int:job_id>')
@login_required
def jobkart_job_detail(job_id):
    conn = get_db_connection()

    # Get job details
    job = conn.execute('''
        SELECT jc.*, e.Name as AssignedToName, o.OrderID, c.CustomerName
        FROM JobCards jc
        LEFT JOIN Employees e ON jc.AssignedTo = e.EmployeeID
        LEFT JOIN Orders o ON jc.RelatedOrderID = o.OrderID
        LEFT JOIN Customers c ON o.CustomerID = c.CustomerID
        WHERE jc.JobCardID = ?
    ''', (job_id,)).fetchone()

    # Get job assignments
    assignments = conn.execute('''
        SELECT ja.*, e.Name as EmployeeName, v.VehicleName, eq.EquipmentName
        FROM JobAssignments ja
        LEFT JOIN Employees e ON ja.AssignedEmployeeID = e.EmployeeID
        LEFT JOIN Vehicles v ON ja.AssignedVehicleID = v.VehicleID
        LEFT JOIN Equipment eq ON ja.AssignedEquipmentID = eq.EquipmentID
        WHERE ja.JobCardID = ?
    ''', (job_id,)).fetchall()

    # Get progress logs
    progress_logs = conn.execute('''
        SELECT jpl.*, e.Name as UpdatedByName
        FROM JobProgressLog jpl
        LEFT JOIN Employees e ON jpl.UpdatedBy = e.EmployeeID
        WHERE jpl.JobCardID = ?
        ORDER BY jpl.UpdateTime DESC
    ''', (job_id,)).fetchall()

    conn.close()

    return render_template('jobkart/job_detail.html', job=job, assignments=assignments, progress_logs=progress_logs)

@app.route('/api/update_job_status', methods=['POST'])
@login_required
def update_job_status():
    data = request.get_json()
    job_id = data['job_id']
    status = data['status']
    notes = data.get('notes', '')

    conn = get_db_connection()
    cursor = conn.cursor()

    # Update job status
    cursor.execute('UPDATE JobCards SET Status = ? WHERE JobCardID = ?', (status, job_id))

    # Add progress log
    cursor.execute('''
        INSERT INTO JobProgressLog (JobCardID, UpdatedBy, UpdateTime, Status, Notes)
        VALUES (?, ?, ?, ?, ?)
    ''', (job_id, session['employee_id'], datetime.now().isoformat(), status, notes))

    # If job completed, update related order status
    if status == 'Completed':
        cursor.execute('''
            UPDATE Orders SET Status = 'Delivered' 
            WHERE OrderID = (SELECT RelatedOrderID FROM JobCards WHERE JobCardID = ?)
        ''', (job_id,))

    log_audit('JobCard', job_id, 'StatusUpdate', session['user_id'], f"Status updated to {status}")

    conn.commit()
    conn.close()

    return jsonify({'success': True})

@app.route('/jobkart/assignments')
@login_required
def jobkart_assignments():
    conn = get_db_connection()
    assignments = conn.execute('''
        SELECT ja.*, jc.JobType, jc.Description, jc.Status as JobStatus, 
               e.Name as EmployeeName, v.VehicleName, eq.EquipmentName
        FROM JobAssignments ja
        JOIN JobCards jc ON ja.JobCardID = jc.JobCardID
        LEFT JOIN Employees e ON ja.AssignedEmployeeID = e.EmployeeID
        LEFT JOIN Vehicles v ON ja.AssignedVehicleID = v.VehicleID
        LEFT JOIN Equipment eq ON ja.AssignedEquipmentID = eq.EquipmentID
        ORDER BY ja.AssignmentID DESC
    ''').fetchall()
    conn.close()
    return render_template('jobkart/assignments.html', assignments=assignments)
