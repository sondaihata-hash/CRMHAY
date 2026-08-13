from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import os

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///crm.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('CRM_SECRET_KEY', 'dev-secret')

db = SQLAlchemy(app)

class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    facebook_id = db.Column(db.String(100), nullable=True)
    email = db.Column(db.String(200), nullable=True)
    phone = db.Column(db.String(50), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Setting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(200), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=True)
    description = db.Column(db.String(400), nullable=True)

def init_db():
    with app.app_context():
        db.create_all()
        # sample data
        if not Customer.query.first():
            sample = Customer(name='Nguyen Van A', facebook_id='fb_12345', email='a@example.com', phone='0123456789', notes='Khách hàng tiềm năng')
            db.session.add(sample)
            db.session.commit()

@app.route('/')
def index():
    return redirect(url_for('customers'))

@app.route('/customers')
def customers():
    q = request.args.get('q', '')
    if q:
        items = Customer.query.filter(Customer.name.contains(q)).all()
    else:
        items = Customer.query.order_by(Customer.created_at.desc()).all()
    return render_template('customers.html', customers=items, q=q)

@app.route('/customers/add', methods=['GET','POST'])
def add_customer():
    if request.method == 'POST':
        name = request.form.get('name')
        facebook_id = request.form.get('facebook_id')
        email = request.form.get('email')
        phone = request.form.get('phone')
        notes = request.form.get('notes')
        if not name:
            flash('Tên là bắt buộc', 'danger')
            return redirect(url_for('add_customer'))
        c = Customer(name=name, facebook_id=facebook_id, email=email, phone=phone, notes=notes)
        db.session.add(c)
        db.session.commit()
        flash('Đã thêm khách hàng', 'success')
        return redirect(url_for('customers'))
    return render_template('customer_form.html', action='add')

@app.route('/customers/<int:c_id>')
def customer_detail(c_id):
    c = Customer.query.get_or_404(c_id)
    return render_template('customer_detail.html', c=c)

@app.route('/customers/<int:c_id>/edit', methods=['GET','POST'])
def edit_customer(c_id):
    c = Customer.query.get_or_404(c_id)
    if request.method == 'POST':
        c.name = request.form.get('name')
        c.facebook_id = request.form.get('facebook_id')
        c.email = request.form.get('email')
        c.phone = request.form.get('phone')
        c.notes = request.form.get('notes')
        db.session.commit()
        flash('Đã cập nhật khách hàng', 'success')
        return redirect(url_for('customer_detail', c_id=c.id))
    return render_template('customer_form.html', action='edit', c=c)

@app.route('/customers/<int:c_id>/delete', methods=['POST'])
def delete_customer(c_id):
    c = Customer.query.get_or_404(c_id)
    db.session.delete(c)
    db.session.commit()
    flash('Đã xóa khách hàng', 'info')
    return redirect(url_for('customers'))

# Settings ("databe con") management
@app.route('/settings')
def settings():
    items = Setting.query.order_by(Setting.key).all()
    return render_template('settings.html', items=items)

@app.route('/settings/add', methods=['POST'])
def add_setting():
    key = request.form.get('key')
    value = request.form.get('value')
    description = request.form.get('description')
    if not key:
        flash('Key là bắt buộc', 'danger')
        return redirect(url_for('settings'))
    s = Setting(key=key, value=value, description=description)
    db.session.add(s)
    try:
        db.session.commit()
        flash('Đã thêm setting', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Lỗi: có thể key đã tồn tại', 'danger')
    return redirect(url_for('settings'))

@app.route('/settings/<int:s_id>/delete', methods=['POST'])
def delete_setting(s_id):
    s = Setting.query.get_or_404(s_id)
    db.session.delete(s)
    db.session.commit()
    flash('Đã xóa setting', 'info')
    return redirect(url_for('settings'))

@app.route('/settings/<int:s_id>/edit', methods=['GET','POST'])
def edit_setting(s_id):
    s = Setting.query.get_or_404(s_id)
    if request.method == 'POST':
        s.value = request.form.get('value')
        s.description = request.form.get('description')
        db.session.commit()
        flash('Đã cập nhật setting', 'success')
        return redirect(url_for('settings'))
    return render_template('setting_form.html', s=s)

# Placeholder for Facebook import integration
@app.route('/facebook/import')
def fb_import_placeholder():
    flash('Tính năng import từ Facebook sẽ được triển khai sau', 'info')
    return redirect(url_for('customers'))

if __name__ == '__main__':
    init_db()
    app.run(debug=True)
else:
    init_db()
