# ERP Routes - Additional routes for ERP functionality
from flask import render_template, request, redirect, url_for, session, flash, jsonify
import sqlite3
from datetime import datetime
from app import app, get_db_connection, login_required, log_audit

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
