from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import sqlite3
import hashlib
from datetime import datetime, date
from functools import wraps

app = Flask(__name__)
app.secret_key = 'your_secret_key_change_in_production'

DATABASE = 'rmc_erp_system.db'

# --- Database helper functions ---
def get_db_connection():
    conn = sqlite3.connect(DATABASE, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def log_audit(conn, entity_type, entity_id, action, user_id, details=""):
    conn.execute('''
        INSERT INTO AuditLog (EntityType, EntityID, Action, PerformedBy, ActionTime, Details)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (entity_type, entity_id, action, user_id, datetime.now().isoformat(), details))

# --- Authentication ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'role' not in session or session['role'] != 'Administrator':
            flash('You do not have permission to access this page.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

# --- Main Routes ---
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

        if user:
            session['user_id'] = user['UserID']
            session['username'] = user['Username']
            session['employee_name'] = user['Name']
            session['role'] = user['RoleName']
            session['employee_id'] = user['EmployeeID']
            
            log_audit(conn, 'User', user['UserID'], 'Login', user['UserID'], f"User {username} logged in")
            conn.commit()
            conn.close()

            flash(f'Welcome {user["Name"]}!', 'success')
            return redirect(url_for('dashboard'))
        else:
            conn.close()
            flash('Invalid username or password', 'danger')

    return render_template('login.html')

@app.route('/logout')
def logout():
    if 'user_id' in session:
        conn = get_db_connection()
        log_audit(conn, 'User', session['user_id'], 'Logout', session['user_id'], f"User {session['username']} logged out")
        conn.commit()
        conn.close()
    session.clear()
    flash('You have been logged out successfully', 'info')
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    conn = get_db_connection()
    stats = {}
    stats['total_orders'] = conn.execute('SELECT COUNT(*) as count FROM Orders').fetchone()['count']
    stats['pending_orders'] = conn.execute("SELECT COUNT(*) as count FROM Orders WHERE Status IN ('Confirmed', 'Pending')").fetchone()['count'] 
    stats['active_jobs'] = conn.execute("SELECT COUNT(*) as count FROM JobCards WHERE Status IN ('Open', 'In Progress')").fetchone()['count']
    stats['total_vehicles'] = conn.execute('SELECT COUNT(*) as count FROM Vehicles').fetchone()['count']
    stats['available_vehicles'] = conn.execute("SELECT COUNT(*) as count FROM Vehicles WHERE Status = 'Available'").fetchone()['count']
    stats['low_inventory'] = conn.execute('SELECT COUNT(*) as count FROM Inventory WHERE CurrentStock <= Threshold').fetchone()['count']
    recent_orders = conn.execute('SELECT o.OrderID, c.CustomerName, p.ProductName, o.Quantity, o.OrderDate, o.Status FROM Orders o JOIN Customers c ON o.CustomerID = c.CustomerID JOIN Products p ON o.ProductID = p.ProductID ORDER BY o.OrderDate DESC LIMIT 5').fetchall()
    recent_jobs = conn.execute('SELECT jc.JobCardID, jc.JobType, jc.Description, jc.Status, jc.Priority, e.Name as AssignedTo FROM JobCards jc LEFT JOIN Employees e ON jc.AssignedTo = e.EmployeeID ORDER BY jc.JobCardID DESC LIMIT 5').fetchall()
    low_inventory = conn.execute('SELECT MaterialName, CurrentStock, Unit, Threshold FROM Inventory WHERE CurrentStock <= Threshold ORDER BY (CurrentStock/Threshold) ASC').fetchall()
    conn.close()
    return render_template('dashboard.html', stats=stats, recent_orders=recent_orders, recent_jobs=recent_jobs, low_inventory=low_inventory)

# --- ERP Routes ---
@app.route('/erp')
@login_required 
def erp_home():
    return render_template('erp/index.html')

@app.route('/erp/orders')
@login_required
def erp_orders():
    conn = get_db_connection()
    orders = conn.execute('SELECT o.*, c.CustomerName, p.ProductName FROM Orders o JOIN Customers c ON o.CustomerID = c.CustomerID JOIN Products p ON o.ProductID = p.ProductID ORDER BY o.OrderDate DESC').fetchall()
    conn.close()
    return render_template('erp/orders.html', orders=orders)

@app.route('/erp/orders/new', methods=['GET', 'POST'])
@login_required
def erp_new_order():
    conn = get_db_connection()
    if request.method == 'POST':
        customer_id = request.form['customer_id']
        product_id = request.form['product_id'] 
        quantity = request.form['quantity']
        delivery_site = request.form['delivery_site']
        scheduled_date = request.form['scheduled_date']
        
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO Orders (CustomerID, ProductID, Quantity, OrderDate, DeliverySite, ScheduledDate, Status, CreatedBy)
            VALUES (?, ?, ?, ?, ?, ?, 'Confirmed', ?)
        ''', (customer_id, product_id, quantity, date.today(), delivery_site, scheduled_date, session['user_id']))
        order_id = cursor.lastrowid
        
        log_audit(conn, 'Order', order_id, 'Create', session['user_id'], f"New order created for quantity {quantity}")
        conn.commit()
        conn.close()
        flash('Order created successfully!', 'success')
        return redirect(url_for('erp_orders'))
    
    customers = conn.execute('SELECT * FROM Customers ORDER BY CustomerName').fetchall()
    products = conn.execute('SELECT * FROM Products ORDER BY ProductName').fetchall()
    conn.close()
    return render_template('erp/new_order.html', customers=customers, products=products)

@app.route('/erp/orders/<int:order_id>')
@login_required
def erp_view_order(order_id):
    conn = get_db_connection()
    order_query = '''
        SELECT o.*, c.CustomerName, c.Address, c.Phone, c.Email, p.ProductName, p.MixDesign
        FROM Orders o
        JOIN Customers c ON o.CustomerID = c.CustomerID
        JOIN Products p ON o.ProductID = p.ProductID
        WHERE o.OrderID = ?
    '''
    order = conn.execute(order_query, (order_id,)).fetchone()
    conn.close()
    if order is None:
        flash('Order not found!', 'danger')
        return redirect(url_for('erp_orders'))
    return render_template('erp/view_order.html', order=order)


@app.route('/erp/orders/edit/<int:order_id>', methods=['GET', 'POST'])
@login_required
def erp_edit_order(order_id):
    conn = get_db_connection()
    if request.method == 'POST':
        customer_id = request.form['customer_id']
        product_id = request.form['product_id']
        quantity = request.form['quantity']
        delivery_site = request.form['delivery_site']
        scheduled_date = request.form['scheduled_date']
        status = request.form['status']
        
        conn.execute('UPDATE Orders SET CustomerID=?, ProductID=?, Quantity=?, DeliverySite=?, ScheduledDate=?, Status=? WHERE OrderID=?',
                     (customer_id, product_id, quantity, delivery_site, scheduled_date, status, order_id))
        log_audit(conn, 'Order', order_id, 'Update', session['user_id'], f"Order #{order_id} updated.")
        conn.commit()
        conn.close()
        flash('Order updated successfully!', 'success')
        return redirect(url_for('erp_orders'))
        
    order = conn.execute('SELECT * FROM Orders WHERE OrderID = ?', (order_id,)).fetchone()
    customers = conn.execute('SELECT * FROM Customers ORDER BY CustomerName').fetchall()
    products = conn.execute('SELECT * FROM Products ORDER BY ProductName').fetchall()
    conn.close()
    return render_template('erp/edit_order.html', order=order, customers=customers, products=products)

@app.route('/erp/orders/delete/<int:order_id>', methods=['POST'])
@login_required
def erp_delete_order(order_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM Orders WHERE OrderID = ?', (order_id,))
    log_audit(conn, 'Order', order_id, 'Delete', session['user_id'], f"Order #{order_id} deleted.")
    conn.commit()
    conn.close()
    flash('Order deleted successfully!', 'danger')
    return redirect(url_for('erp_orders'))

@app.route('/erp/inventory', methods=['GET', 'POST'])
@login_required
def erp_inventory():
    conn = get_db_connection()
    if request.method == 'POST':
        material_id = request.form.get('materialId')
        name = request.form.get('materialName')
        supplier_id = request.form.get('supplierId') or None
        stock = request.form.get('currentStock')
        unit = request.form.get('unit')
        threshold = request.form.get('threshold')
        if material_id:
            conn.execute('UPDATE Inventory SET MaterialName=?, SupplierID=?, CurrentStock=?, Unit=?, Threshold=?, LastUpdated=? WHERE MaterialID=?', (name, supplier_id, stock, unit, threshold, date.today(), material_id))
            flash('Material updated!', 'success')
        else:
            conn.execute('INSERT INTO Inventory (MaterialName, SupplierID, CurrentStock, Unit, Threshold, LastUpdated) VALUES (?, ?, ?, ?, ?, ?)', (name, supplier_id, stock, unit, threshold, date.today()))
            flash('New material added!', 'success')
        conn.commit()
        conn.close()
        return redirect(url_for('erp_inventory'))
    
    inventory = conn.execute("SELECT i.*, s.SupplierName, CASE WHEN i.CurrentStock <= i.Threshold THEN 'Low Stock' ELSE 'In Stock' END as StockStatus FROM Inventory i LEFT JOIN Suppliers s ON i.SupplierID = s.SupplierID ORDER BY i.MaterialName").fetchall()
    suppliers = conn.execute('SELECT * FROM Suppliers ORDER BY SupplierName').fetchall()
    conn.close()
    return render_template('erp/inventory.html', inventory=inventory, suppliers=suppliers)

@app.route('/erp/production', methods=['GET', 'POST'])
@login_required
def erp_production():
    conn = get_db_connection()
    batches = conn.execute('SELECT pb.*, o.OrderID, c.CustomerName, p.ProductName, l.LocationName, e.Name as CreatedByName FROM ProductionBatch pb LEFT JOIN Orders o ON pb.OrderID = o.OrderID LEFT JOIN Customers c ON o.CustomerID = c.CustomerID LEFT JOIN Products p ON pb.ProductID = p.ProductID LEFT JOIN Locations l ON pb.PlantLocationID = l.LocationID LEFT JOIN Users u ON pb.CreatedBy = u.UserID LEFT JOIN Employees e ON u.EmployeeID = e.EmployeeID ORDER BY pb.BatchTime DESC').fetchall()
    conn.close()
    return render_template('erp/production.html', batches=batches)

@app.route('/erp/production/new', methods=['GET', 'POST'])
@login_required
def erp_new_batch():
    conn = get_db_connection()
    if request.method == 'POST':
        order_id = request.form.get('orderId')
        product_id = request.form.get('productId')
        quantity = request.form.get('quantity')
        location_id = request.form.get('locationId')
        status = request.form.get('status')
        
        conn.execute('INSERT INTO ProductionBatch (OrderID, ProductID, QuantityBatch, PlantLocationID, BatchTime, Status, CreatedBy) VALUES (?, ?, ?, ?, ?, ?, ?)',
                     (order_id, product_id, quantity, location_id, datetime.now(), status, session['user_id']))
        conn.commit()
        conn.close()
        flash('New production batch created!', 'success')
        return redirect(url_for('erp_production'))

    orders = conn.execute('SELECT * FROM Orders WHERE Status IN ("Confirmed", "In Production")').fetchall()
    products = conn.execute('SELECT * FROM Products').fetchall()
    locations = conn.execute('SELECT * FROM Locations').fetchall()
    conn.close()
    return render_template('erp/new_batch.html', orders=orders, products=products, locations=locations)

@app.route('/erp/production/view/<int:batch_id>')
@login_required
def erp_view_batch(batch_id):
    conn = get_db_connection()
    query = '''
        SELECT pb.*, o.OrderID, c.CustomerName, p.ProductName, l.LocationName, e.Name as CreatedByName 
        FROM ProductionBatch pb 
        LEFT JOIN Orders o ON pb.OrderID = o.OrderID 
        LEFT JOIN Customers c ON o.CustomerID = c.CustomerID 
        LEFT JOIN Products p ON pb.ProductID = p.ProductID 
        LEFT JOIN Locations l ON pb.PlantLocationID = l.LocationID 
        LEFT JOIN Users u ON pb.CreatedBy = u.UserID 
        LEFT JOIN Employees e ON u.EmployeeID = e.EmployeeID 
        WHERE pb.BatchID = ?
    '''
    batch = conn.execute(query, (batch_id,)).fetchone()
    conn.close()
    if batch is None:
        flash(f'Batch #{batch_id} not found.', 'danger')
        return redirect(url_for('erp_production'))
    return render_template('erp/view_batch.html', batch=batch)

@app.route('/erp/production/qc/<int:batch_id>', methods=['GET', 'POST'])
@login_required
def erp_quality_control(batch_id):
    conn = get_db_connection()
    if request.method == 'POST':
        test_type = request.form['test_type']
        result = request.form['result']
        remarks = request.form.get('remarks', '')
        
        conn.execute('''
            INSERT INTO QualityControl (BatchID, TestType, TestDate, Result, TestedBy, Remarks)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (batch_id, test_type, datetime.now(), result, session['employee_id'], remarks))
        conn.commit()
        flash('New QC record added successfully!', 'success')
        conn.close()
        return redirect(url_for('erp_quality_control', batch_id=batch_id))

    batch = conn.execute('SELECT * FROM ProductionBatch WHERE BatchID = ?', (batch_id,)).fetchone()
    qc_records = conn.execute('SELECT qc.*, e.Name as TestedBy FROM QualityControl qc JOIN Employees e ON qc.TestedBy = e.EmployeeID WHERE qc.BatchID = ? ORDER BY qc.TestDate DESC', (batch_id,)).fetchall()
    conn.close()
    if batch is None:
        flash(f'Batch #{batch_id} not found.', 'danger')
        return redirect(url_for('erp_production'))
    return render_template('erp/quality_control.html', batch=batch, qc_records=qc_records)

@app.route('/erp/vehicles', methods=['GET', 'POST'])
@login_required
def erp_vehicles():
    conn = get_db_connection()
    if request.method == 'POST':
        vehicle_id = request.form.get('vehicleId')
        name = request.form.get('vehicleName')
        reg_no = request.form.get('registrationNo')
        v_type = request.form.get('type')
        status = request.form.get('status')
        capacity = request.form.get('capacity')
        if vehicle_id:
            conn.execute('UPDATE Vehicles SET VehicleName=?, RegistrationNo=?, Type=?, Status=?, Capacity=? WHERE VehicleID=?', (name, reg_no, v_type, status, capacity, vehicle_id))
            flash('Vehicle updated!', 'success')
        else:
            conn.execute('INSERT INTO Vehicles (VehicleName, RegistrationNo, Type, Status, Capacity) VALUES (?, ?, ?, ?, ?)', (name, reg_no, v_type, status, capacity))
            flash('New vehicle added!', 'success')
        conn.commit()
        conn.close()
        return redirect(url_for('erp_vehicles'))

    vehicles = conn.execute('SELECT v.*, jc.JobCardID, jc.JobType FROM Vehicles v LEFT JOIN JobAssignments ja ON v.VehicleID = ja.AssignedVehicleID LEFT JOIN JobCards jc ON ja.JobCardID = jc.JobCardID AND jc.Status IN ("Open", "In Progress") ORDER BY v.VehicleName').fetchall()
    conn.close()
    return render_template('erp/vehicles.html', vehicles=vehicles)

@app.route('/erp/vehicles/delete/<int:vehicle_id>', methods=['POST'])
@login_required
def erp_delete_vehicle(vehicle_id):
    conn = get_db_connection()
    try:
        conn.execute('DELETE FROM Vehicles WHERE VehicleID = ?', (vehicle_id,))
        log_audit(conn, 'Vehicle', vehicle_id, 'Delete', session['user_id'], f"Vehicle ID #{vehicle_id} deleted.")
        conn.commit()
        flash('Vehicle deleted successfully!', 'danger')
    except Exception as e:
        flash(f'Error deleting vehicle: {e}', 'danger')
    finally:
        conn.close()
    return redirect(url_for('erp_vehicles'))

@app.route('/erp/employees', methods=['GET', 'POST'])
@login_required
def erp_employees():
    conn = get_db_connection()
    if request.method == 'POST':
        employee_id = request.form.get('employeeId')
        name = request.form.get('name')
        role_id = request.form.get('roleId')
        dept_id = request.form.get('departmentId')
        phone = request.form.get('phone')
        email = request.form.get('email')
        status = request.form.get('status')
        if employee_id:
            conn.execute('UPDATE Employees SET Name=?, RoleID=?, DepartmentID=?, Phone=?, Email=?, Status=? WHERE EmployeeID=?', (name, role_id, dept_id, phone, email, status, employee_id))
            flash('Employee updated!', 'success')
        else:
            conn.execute('INSERT INTO Employees (Name, RoleID, DepartmentID, Phone, Email, DateOfJoining, Status) VALUES (?, ?, ?, ?, ?, ?, ?)', (name, role_id, dept_id, phone, email, date.today(), status))
            flash('New employee added!', 'success')
        conn.commit()
        conn.close()
        return redirect(url_for('erp_employees'))

    employees = conn.execute('SELECT e.*, r.RoleName, d.DepartmentName FROM Employees e LEFT JOIN Roles r ON e.RoleID = r.RoleID LEFT JOIN Departments d ON e.DepartmentID = d.DepartmentID ORDER BY e.Name').fetchall()
    roles = conn.execute('SELECT * FROM Roles').fetchall()
    departments = conn.execute('SELECT * FROM Departments').fetchall()
    conn.close()
    return render_template('erp/employees.html', employees=employees, roles=roles, departments=departments)

@app.route('/erp/employees/delete/<int:employee_id>', methods=['POST'])
@login_required
def erp_delete_employee(employee_id):
    conn = get_db_connection()
    try:
        conn.execute('DELETE FROM Users WHERE EmployeeID = ?', (employee_id,))
        conn.execute('DELETE FROM Employees WHERE EmployeeID = ?', (employee_id,))
        log_audit(conn, 'Employee', employee_id, 'Delete', session['user_id'], f"Employee ID #{employee_id} deleted.")
        conn.commit()
        flash('Employee deleted successfully!', 'danger')
    except Exception as e:
        flash(f'Error deleting employee: {e}', 'danger')
    finally:
        conn.close()
    return redirect(url_for('erp_employees'))

# --- NEW ROUTES FOR USER MANAGEMENT ---
@app.route('/erp/users', methods=['GET', 'POST'])
@login_required
@admin_required
def erp_users():
    conn = get_db_connection()
    if request.method == 'POST':
        employee_id = request.form['employee_id']
        username = request.form['username']
        password = request.form['password']
        hashed_password = hash_password(password)

        conn.execute('INSERT INTO Users (EmployeeID, Username, PasswordHash) VALUES (?, ?, ?)',
                     (employee_id, username, hashed_password))
        conn.commit()
        flash(f'User account for {username} created successfully!', 'success')
        conn.close()
        return redirect(url_for('erp_users'))

    users = conn.execute('SELECT u.UserID, u.Username, e.Name, r.RoleName FROM Users u JOIN Employees e ON u.EmployeeID = e.EmployeeID JOIN Roles r ON e.RoleID = r.RoleID').fetchall()
    available_employees = conn.execute('SELECT e.*, r.RoleName FROM Employees e JOIN Roles r ON e.RoleID = r.RoleID WHERE e.EmployeeID NOT IN (SELECT EmployeeID FROM Users WHERE EmployeeID IS NOT NULL)').fetchall()
    conn.close()
    return render_template('erp/users.html', users=users, available_employees=available_employees)

@app.route('/erp/users/delete/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def erp_delete_user(user_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM Users WHERE UserID = ?', (user_id,))
    conn.commit()
    conn.close()
    flash('User account deleted successfully.', 'danger')
    return redirect(url_for('erp_users'))
# ------------------------------------

# --- Job Kart Routes ---
@app.route('/jobkart')
@login_required
def jobkart_home():
    return render_template('jobkart/index.html')

@app.route('/jobkart/jobs')
@login_required
def jobkart_jobs():
    conn = get_db_connection()
    jobs = conn.execute('SELECT jc.*, e.Name as AssignedTo FROM JobCards jc LEFT JOIN Employees e ON jc.AssignedTo = e.EmployeeID ORDER BY jc.ScheduledStart DESC').fetchall()
    employees = conn.execute('SELECT * FROM Employees WHERE Status="Active"').fetchall()
    orders = conn.execute('SELECT * FROM Orders WHERE Status IN ("Confirmed", "In Production")').fetchall()
    conn.close()
    return render_template('jobkart/jobs.html', jobs=jobs, employees=employees, orders=orders)

@app.route('/jobkart/jobs/new', methods=['GET', 'POST'])
@login_required
def jobkart_new_job():
    conn = get_db_connection()
    if request.method == 'POST':
        job_type = request.form['job_type']
        description = request.form['description']
        assigned_to = request.form['assigned_to']
        priority = request.form['priority']
        scheduled_start = request.form['scheduled_start']
        scheduled_end = request.form['scheduled_end']
        related_order = request.form.get('related_order') or None
        
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO JobCards (RelatedOrderID, JobType, Description, AssignedTo, Status, Priority, ScheduledStart, ScheduledEnd)
            VALUES (?, ?, ?, ?, 'Open', ?, ?, ?)
        ''', (related_order, job_type, description, assigned_to, priority, scheduled_start, scheduled_end))
        job_id = cursor.lastrowid
        
        log_audit(conn, 'JobCard', job_id, 'Create', session['user_id'], f"New job card created: {job_type}")
        conn.commit()
        conn.close()
        flash('Job Card created successfully!', 'success')
        return redirect(url_for('jobkart_jobs'))
    
    employees = conn.execute('SELECT * FROM Employees WHERE Status="Active"').fetchall()
    orders = conn.execute('SELECT * FROM Orders WHERE Status IN ("Confirmed", "In Production")').fetchall()
    conn.close()
    return render_template('jobkart/new_job.html', employees=employees, orders=orders)

@app.route('/jobkart/jobs/<int:job_id>')
@login_required
def jobkart_job_detail(job_id):
    conn = get_db_connection()
    job = conn.execute('SELECT jc.*, e.Name as AssignedToName FROM JobCards jc LEFT JOIN Employees e ON jc.AssignedTo = e.EmployeeID WHERE jc.JobCardID = ?', (job_id,)).fetchone()
    assignments = conn.execute('SELECT ja.*, e.Name as EmployeeName, v.VehicleName, eq.EquipmentName FROM JobAssignments ja LEFT JOIN Employees e ON ja.AssignedEmployeeID = e.EmployeeID LEFT JOIN Vehicles v ON ja.AssignedVehicleID = v.VehicleID LEFT JOIN Equipment eq ON ja.AssignedEquipmentID = eq.EquipmentID WHERE ja.JobCardID = ?', (job_id,)).fetchall()
    progress_logs = conn.execute('SELECT jpl.*, e.Name as UpdatedByName FROM JobProgressLog jpl LEFT JOIN Employees e ON jpl.UpdatedBy = e.EmployeeID WHERE jpl.JobCardID = ? ORDER BY jpl.UpdateTime DESC', (job_id,)).fetchall()
    conn.close()
    return render_template('jobkart/job_detail.html', job=job, assignments=assignments, progress_logs=progress_logs)

@app.route('/jobkart/jobs/delete/<int:job_id>', methods=['POST'])
@login_required
def jobkart_delete_job(job_id):
    conn = get_db_connection()
    try:
        conn.execute('DELETE FROM JobAssignments WHERE JobCardID = ?', (job_id,))
        conn.execute('DELETE FROM JobProgressLog WHERE JobCardID = ?', (job_id,))
        conn.execute('DELETE FROM JobCards WHERE JobCardID = ?', (job_id,))
        log_audit(conn, 'JobCard', job_id, 'Delete', session['user_id'], f"Job Card #{job_id} deleted.")
        conn.commit()
        flash('Job Card deleted successfully!', 'danger')
    except Exception as e:
        flash(f'Error deleting job card: {e}', 'danger')
    finally:
        conn.close()
    return redirect(url_for('jobkart_jobs'))

@app.route('/jobkart/assignments', methods=['GET', 'POST'])
@login_required
def jobkart_assignments():
    conn = get_db_connection()
    
    assignments = conn.execute('''
        SELECT ja.*, jc.Description, jc.JobType, jc.Status as JobStatus, e.Name as EmployeeName, v.VehicleName, eq.EquipmentName 
        FROM JobAssignments ja 
        JOIN JobCards jc ON ja.JobCardID = jc.JobCardID 
        LEFT JOIN Employees e ON ja.AssignedEmployeeID = e.EmployeeID 
        LEFT JOIN Vehicles v ON ja.AssignedVehicleID = v.VehicleID 
        LEFT JOIN Equipment eq ON ja.AssignedEquipmentID = eq.EquipmentID 
        ORDER BY ja.AssignmentID DESC
    ''').fetchall()
    
    # Data for the edit modal dropdowns
    employees = conn.execute('SELECT * FROM Employees WHERE Status="Active"').fetchall()
    vehicles = conn.execute('SELECT * FROM Vehicles WHERE Status="Available"').fetchall()
    equipment = conn.execute('SELECT * FROM Equipment WHERE Status="Operational"').fetchall()
    
    conn.close()
    return render_template('jobkart/assignments.html', assignments=assignments, employees=employees, vehicles=vehicles, equipment=equipment)

@app.route('/jobkart/assignments/edit/<int:assignment_id>', methods=['POST'])
@login_required
def jobkart_edit_assignment(assignment_id):
    conn = get_db_connection()
    try:
        employee_id = request.form.get('employee_id') or None
        role = request.form.get('role_in_job')
        vehicle_id = request.form.get('vehicle_id') or None
        equipment_id = request.form.get('equipment_id') or None

        conn.execute('''
            UPDATE JobAssignments 
            SET AssignedEmployeeID = ?, RoleInJob = ?, AssignedVehicleID = ?, AssignedEquipmentID = ?
            WHERE AssignmentID = ?
        ''', (employee_id, role, vehicle_id, equipment_id, assignment_id))
        conn.commit()
        flash('Assignment updated successfully!', 'success')
    except Exception as e:
        flash(f'Error updating assignment: {e}', 'danger')
    finally:
        conn.close()
    return redirect(url_for('jobkart_assignments'))

@app.route('/jobkart/assignments/delete/<int:assignment_id>', methods=['POST'])
@login_required
def jobkart_delete_assignment(assignment_id):
    conn = get_db_connection()
    try:
        conn.execute('DELETE FROM JobAssignments WHERE AssignmentID = ?', (assignment_id,))
        conn.commit()
        flash('Assignment removed successfully!', 'danger')
    except Exception as e:
        flash(f'Error removing assignment: {e}', 'danger')
    finally:
        conn.close()
    return redirect(url_for('jobkart_assignments'))


# --- Integration and API Routes ---
@app.route('/integration')
@login_required
def integration_home():
    conn = get_db_connection()
    events = conn.execute('SELECT ie.*, o.OrderID, c.CustomerName, jc.JobType FROM IntegrationEvents ie LEFT JOIN Orders o ON ie.RelatedOrderID = o.OrderID LEFT JOIN Customers c ON o.CustomerID = c.CustomerID LEFT JOIN JobCards jc ON ie.JobCardID = jc.JobCardID ORDER BY ie.EventTime DESC LIMIT 20').fetchall()
    conn.close()
    return render_template('integration/events.html', events=events)

@app.route('/api/update_job_status', methods=['POST'])
@login_required
def update_job_status():
    data = request.get_json()
    job_id = data['job_id']
    status = data['status']
    notes = data.get('notes', '')
    conn = get_db_connection()
    conn.execute('UPDATE JobCards SET Status = ? WHERE JobCardID = ?', (status, job_id))
    conn.execute('INSERT INTO JobProgressLog (JobCardID, UpdatedBy, UpdateTime, Status, Notes) VALUES (?, ?, ?, ?, ?)', (job_id, session['employee_id'], datetime.now(), status, notes))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/auto_create_jobs', methods=['POST'])
@login_required
def auto_create_jobs():
    conn = get_db_connection()
    orders_without_jobs = conn.execute("SELECT * FROM Orders WHERE Status = 'Confirmed' AND OrderID NOT IN (SELECT RelatedOrderID FROM JobCards WHERE RelatedOrderID IS NOT NULL)").fetchall()
    created_count = 0
    for order in orders_without_jobs:
        description = f"Deliver {order['Quantity']} units to {order['DeliverySite']}"
        scheduled_start = f"{order['ScheduledDate']} 08:00:00"
        scheduled_end = f"{order['ScheduledDate']} 17:00:00"
        conn.execute("INSERT INTO JobCards (RelatedOrderID, JobType, Description, AssignedTo, Status, Priority, ScheduledStart, ScheduledEnd) VALUES (?, 'Delivery', ?, 5, 'Open', 'Medium', ?, ?)",
                     (order['OrderID'], description, scheduled_start, scheduled_end))
        created_count += 1
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'created_jobs': created_count})

@app.route('/api/sync_inventory', methods=['POST'])
@login_required
def sync_inventory():
    conn = get_db_connection()
    # This is a placeholder for a more complex inventory sync logic
    flash('Inventory sync feature is not yet implemented.', 'info')
    conn.close()
    return jsonify({'success': True, 'synced_jobs': 0})

if __name__ == '__main__':
    app.run(debug=True, port=5000)
