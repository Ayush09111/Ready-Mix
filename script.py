# Create the complete main Flask application with all routes
complete_app_code = """from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import sqlite3
import hashlib
from datetime import datetime, timedelta
import os
from functools import wraps

app = Flask(__name__)
app.secret_key = 'your_secret_key_change_in_production'

DATABASE = 'rmc_erp_system.db'

# Database helper functions
def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def log_audit(entity_type, entity_id, action, user_id, details=""):
    conn = get_db_connection()
    conn.execute('''
        INSERT INTO AuditLog (EntityType, EntityID, Action, PerformedBy, ActionTime, Details)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (entity_type, entity_id, action, user_id, datetime.now().isoformat(), details))
    conn.commit()
    conn.close()

# Authentication decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Main Routes
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        hashed_password = hash_password(password)
        
        conn = get_db_connection()
        user = conn.execute('''
            SELECT u.UserID, u.Username, u.EmployeeID, e.Name, r.RoleName 
            FROM Users u 
            JOIN Employees e ON u.EmployeeID = e.EmployeeID 
            JOIN Roles r ON e.RoleID = r.RoleID
            WHERE u.Username = ? AND u.PasswordHash = ?
        ''', (username, hashed_password)).fetchone()
        conn.close()
        
        if user:
            session['user_id'] = user['UserID']
            session['username'] = user['Username']
            session['employee_name'] = user['Name']
            session['role'] = user['RoleName']
            session['employee_id'] = user['EmployeeID']
            
            log_audit('User', user['UserID'], 'Login', user['UserID'], f"User {username} logged in")
            flash(f'Welcome {user["Name"]}!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password', 'danger')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    if 'user_id' in session:
        log_audit('User', session['user_id'], 'Logout', session['user_id'], f"User {session['username']} logged out")
    session.clear()
    flash('You have been logged out successfully', 'info')
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    conn = get_db_connection()
    
    # Get dashboard statistics
    stats = {}
    stats['total_orders'] = conn.execute('SELECT COUNT(*) as count FROM Orders').fetchone()['count']
    stats['pending_orders'] = conn.execute("SELECT COUNT(*) as count FROM Orders WHERE Status IN ('Confirmed', 'Pending')").fetchone()['count'] 
    stats['active_jobs'] = conn.execute("SELECT COUNT(*) as count FROM JobCards WHERE Status IN ('Open', 'In Progress')").fetchone()['count']
    stats['total_vehicles'] = conn.execute('SELECT COUNT(*) as count FROM Vehicles').fetchone()['count']
    stats['available_vehicles'] = conn.execute("SELECT COUNT(*) as count FROM Vehicles WHERE Status = 'Available'").fetchone()['count']
    stats['low_inventory'] = conn.execute('SELECT COUNT(*) as count FROM Inventory WHERE CurrentStock <= Threshold').fetchone()['count']
    
    # Get recent orders
    recent_orders = conn.execute('''
        SELECT o.OrderID, c.CustomerName, p.ProductName, o.Quantity, o.OrderDate, o.Status
        FROM Orders o
        JOIN Customers c ON o.CustomerID = c.CustomerID
        JOIN Products p ON o.ProductID = p.ProductID
        ORDER BY o.OrderDate DESC LIMIT 5
    ''').fetchall()
    
    # Get recent job cards
    recent_jobs = conn.execute('''
        SELECT jc.JobCardID, jc.JobType, jc.Description, jc.Status, jc.Priority, e.Name as AssignedTo
        FROM JobCards jc
        LEFT JOIN Employees e ON jc.AssignedTo = e.EmployeeID
        ORDER BY jc.JobCardID DESC LIMIT 5
    ''').fetchall()
    
    # Get low inventory items
    low_inventory = conn.execute('''
        SELECT MaterialName, CurrentStock, Unit, Threshold
        FROM Inventory 
        WHERE CurrentStock <= Threshold
        ORDER BY (CurrentStock/Threshold) ASC
    ''').fetchall()
    
    conn.close()
    
    return render_template('dashboard.html', 
                         stats=stats, 
                         recent_orders=recent_orders,
                         recent_jobs=recent_jobs,
                         low_inventory=low_inventory)

# ERP Routes
@app.route('/erp')
@login_required 
def erp_home():
    return render_template('erp/index.html')

@app.route('/erp/orders')
@login_required
def erp_orders():
    conn = get_db_connection()
    orders = conn.execute('''
        SELECT o.OrderID, c.CustomerName, p.ProductName, o.Quantity, o.OrderDate, 
               o.DeliverySite, o.ScheduledDate, o.Status
        FROM Orders o
        JOIN Customers c ON o.CustomerID = c.CustomerID
        JOIN Products p ON o.ProductID = p.ProductID
        ORDER BY o.OrderDate DESC
    ''').fetchall()
    conn.close()
    return render_template('erp/orders.html', orders=orders)

@app.route('/erp/orders/new', methods=['GET', 'POST'])
@login_required
def erp_new_order():
    if request.method == 'POST':
        customer_id = request.form['customer_id']
        product_id = request.form['product_id'] 
        quantity = request.form['quantity']
        delivery_site = request.form['delivery_site']
        scheduled_date = request.form['scheduled_date']
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO Orders (CustomerID, ProductID, Quantity, OrderDate, DeliverySite, ScheduledDate, Status, CreatedBy)
            VALUES (?, ?, ?, ?, ?, ?, 'Confirmed', ?)
        ''', (customer_id, product_id, quantity, datetime.now().date(), delivery_site, scheduled_date, session['user_id']))
        
        order_id = cursor.lastrowid
        
        # Log the action
        log_audit('Order', order_id, 'Create', session['user_id'], f"New order created for quantity {quantity}")
        
        # Create integration event to trigger job card creation
        cursor.execute('''
            INSERT INTO IntegrationEvents (RelatedOrderID, EventType, EventTime, Details)
            VALUES (?, 'OrderCreated', ?, 'Order created, awaiting job card generation')
        ''', (order_id, datetime.now().isoformat()))
        
        conn.commit()
        conn.close()
        
        flash('Order created successfully!', 'success')
        return redirect(url_for('erp_orders'))
    
    conn = get_db_connection()
    customers = conn.execute('SELECT * FROM Customers ORDER BY CustomerName').fetchall()
    products = conn.execute('SELECT * FROM Products ORDER BY ProductName').fetchall()
    conn.close()
    
    return render_template('erp/new_order.html', customers=customers, products=products)

@app.route('/erp/inventory')
@login_required
def erp_inventory():
    conn = get_db_connection()
    inventory = conn.execute('''
        SELECT i.MaterialID, i.MaterialName, i.CurrentStock, i.Unit, i.Threshold, 
               s.SupplierName, i.LastUpdated,
               CASE WHEN i.CurrentStock <= i.Threshold THEN 'Low Stock' ELSE 'Normal' END as StockStatus
        FROM Inventory i
        LEFT JOIN Suppliers s ON i.SupplierID = s.SupplierID
        ORDER BY i.MaterialName
    ''').fetchall()
    conn.close()
    return render_template('erp/inventory.html', inventory=inventory)

@app.route('/erp/production')
@login_required
def erp_production():
    conn = get_db_connection()
    batches = conn.execute('''
        SELECT pb.BatchID, o.OrderID, c.CustomerName, p.ProductName, pb.QuantityBatch,
               l.LocationName, pb.BatchTime, pb.Status, e.Name as CreatedBy
        FROM ProductionBatch pb
        LEFT JOIN Orders o ON pb.OrderID = o.OrderID
        LEFT JOIN Customers c ON o.CustomerID = c.CustomerID
        LEFT JOIN Products p ON pb.ProductID = p.ProductID
        LEFT JOIN Locations l ON pb.PlantLocationID = l.LocationID
        LEFT JOIN Users u ON pb.CreatedBy = u.UserID
        LEFT JOIN Employees e ON u.EmployeeID = e.EmployeeID
        ORDER BY pb.BatchTime DESC
    ''').fetchall()
    conn.close()
    return render_template('erp/production.html', batches=batches)

@app.route('/erp/vehicles')
@login_required
def erp_vehicles():
    conn = get_db_connection()
    vehicles = conn.execute('''
        SELECT v.*, ja.JobCardID, jc.JobType, jc.Status as JobStatus
        FROM Vehicles v
        LEFT JOIN JobAssignments ja ON v.VehicleID = ja.AssignedVehicleID
        LEFT JOIN JobCards jc ON ja.JobCardID = jc.JobCardID AND jc.Status IN ('Open', 'In Progress')
        ORDER BY v.VehicleName
    ''').fetchall()
    conn.close()
    return render_template('erp/vehicles.html', vehicles=vehicles)

@app.route('/erp/employees')
@login_required
def erp_employees():
    conn = get_db_connection()
    employees = conn.execute('''
        SELECT e.*, r.RoleName, d.DepartmentName
        FROM Employees e
        LEFT JOIN Roles r ON e.RoleID = r.RoleID
        LEFT JOIN Departments d ON e.DepartmentID = d.DepartmentID
        ORDER BY e.Name
    ''').fetchall()
    conn.close()
    return render_template('erp/employees.html', employees=employees)

# Job Kart Routes
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

# Integration Routes
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

# API Routes
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

@app.route('/api/auto_create_jobs', methods=['POST'])
@login_required
def auto_create_jobs():
    \"\"\"API endpoint to automatically create job cards for confirmed orders\"\"\"
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
    \"\"\"API endpoint to sync inventory based on completed jobs\"\"\"
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

if __name__ == '__main__':
    app.run(debug=True, port=5000)
"""

# Write the complete application
with open('app.py', 'w') as f:
    f.write(complete_app_code)

print("✓ Complete Flask application created with all routes integrated!")
print("✓ The application includes:")
print("  - User authentication and session management")
print("  - Complete ERP modules (Orders, Inventory, Production, Vehicles, Employees)")
print("  - Complete Job Kart modules (Job Cards, Assignments, Progress tracking)")
print("  - Integration APIs and automation")
print("  - RESTful APIs for AJAX updates")
print("  - Comprehensive audit logging")
print("  - Dashboard with real-time statistics")
print("  - Professional HTML templates with Bootstrap")
print("  - Custom CSS and JavaScript")
print("  - Database with sample data")
print("\n✓ Ready to run the application!")