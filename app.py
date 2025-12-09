import requests
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_pymongo import PyMongo
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta, timezone
from functools import wraps
from bson.objectid import ObjectId
import os
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY")
app.config["MONGO_URI"] = os.environ.get("MONGO_URI")

if not app.config["MONGO_URI"]:
    raise ValueError("MONGO_URI 环境变量没有配置或为空!")

mongo = PyMongo(app)

# 验证MongoDB连接和集合
try:
    # 测试数据库连接
    mongo.db.command('ping')
    print("成功连接到MongoDB!")

    # 确保events集合存在
    if 'events' not in mongo.db.list_collection_names():
        mongo.db.create_collection('events')
        print("创建events集合")

    # 创建索引以提高性能
    mongo.db.events.create_index([('datetime', 1)])
    mongo.db.events.create_index([('organizer_id', 1)])
    print("创建必要的索引")
except Exception as e:
    print(f"连接MongoDB时出错: {str(e)}")
    print(f"错误类型: {type(e)}")
    import traceback

    print(f"追踪: {traceback.format_exc()}")


# 登录要求装饰器
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('请登录以访问此页面。', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)

    return decorated_function


def get_emergency_contact(user):
    """获取紧急联系人信息的辅助函数"""
    emergency_contact = {
        'name': None,
        'phone': None,
        'type': None
    }

    # 首先尝试获取关联子女的联系方式
    if user['role'] == 'elder':
        linked_child = mongo.db.users.find_one({
            'elder_id': str(user['_id']),
            'role': 'child'
        })

        if linked_child and linked_child.get('phone'):
            emergency_contact = {
                'name': linked_child['name'],
                'phone': linked_child['phone'],
                'type': 'child'
            }
            print(f"使用关联子女的联系方式: {linked_child['name']} - {linked_child['phone']}")
        # 如果没有关联子女或没有电话，使用紧急联系人
        elif user.get('emergency_contact'):
            emergency_contact = {
                'name': '紧急联系人',
                'phone': user['emergency_contact'],
                'type': 'emergency'
            }
            print(f"使用紧急联系人: {user['emergency_contact']}")

    return emergency_contact


@app.route('/')
def index():
    print(">>> 收到访问请求！正在处理...")
    if 'user_id' in session:
        try:
            user = mongo.db.users.find_one({'_id': ObjectId(session['user_id'])})
            if user:
                if user['role'] == 'child':
                    return redirect(url_for('child_dashboard'))
                else:
                    emergency_contact = get_emergency_contact(user)
                    return render_template('dashboard.html',
                                           user=user,
                                           emergency_contact=emergency_contact)
        except Exception as e:
            print(f"首页路由错误: {str(e)}")
            flash('加载主界面时出错', 'error')
    return render_template('index.html')


@app.route('/db')
@login_required
def dashboard():
    try:
        user = mongo.db.users.find_one({'_id': ObjectId(session['user_id'])})
        if not user:
            flash('用户未找到', 'error')
            return redirect(url_for('index'))

        if user['role'] == 'child':
            return redirect(url_for('child_dashboard'))

        emergency_contact = get_emergency_contact(user)

        # 获取今日药品
        today = datetime.now()
        today_start = datetime.combine(today.date(), datetime.min.time())
        today_end = datetime.combine(today.date(), datetime.max.time())

        today_medicines = list(mongo.db.medicine_schedule.find({
            'user_id': ObjectId(session['user_id']),
            'date': {
                '$gte': today_start,
                '$lte': today_end
            },
            'is_taken': False
        }).sort('time', 1))

        # 获取即将到来的提醒（未来3天）
        three_days_later = today + timedelta(days=3)
        upcoming_reminders = list(mongo.db.reminders.find({
            'user_id': ObjectId(session['user_id']),
            'completed': False,
            'date': {
                '$gte': today.strftime('%Y-%m-%d'),
                '$lte': three_days_later.strftime('%Y-%m-%d')
            }
        }).sort([('date', 1), ('time', 1)]))

        # 获取即将到期的账单（未来7天）
        seven_days_later = today + timedelta(days=7)
        upcoming_bills = list(mongo.db.fixed_expenses.find({
            'user_id': ObjectId(session['user_id']),
            'is_paid': False,
            'date': {
                '$gte': today,
                '$lte': seven_days_later
            }
        }).sort('date', 1))

        return render_template('dashboard.html',
                               user=user,
                               emergency_contact=emergency_contact,
                               today_medicines=today_medicines,
                               upcoming_reminders=upcoming_reminders,
                               upcoming_bills=upcoming_bills)
    except Exception as e:
        print(f"主界面路由错误: {str(e)}")
        flash('加载主界面时出错', 'error')
        return redirect(url_for('index'))


@app.route('/cd')
@login_required
def child_dashboard():
    try:
        user = mongo.db.users.find_one({'_id': ObjectId(session['user_id'])})
        if not user or user['role'] != 'child':
            flash('访问被拒绝', 'error')
            return redirect(url_for('index'))

        # 获取关联的老年人信息
        linked_elder = None
        if user.get('elder_id'):
            linked_elder = mongo.db.users.find_one({'_id': ObjectId(user['elder_id'])})

            if linked_elder:
                # 获取老年人的即将到来的活动
                elder_events = list(mongo.db.events.find({
                    'participants': ObjectId(user['elder_id'])
                }).sort('datetime', 1))

                # 获取老年人的今日药品
                today = datetime.now()
                today_start = datetime.combine(today.date(), datetime.min.time())
                today_end = datetime.combine(today.date(), datetime.max.time())

                today_medicines = list(mongo.db.medicine_schedule.find({
                    'user_id': ObjectId(user['elder_id']),
                    'date': {
                        '$gte': today_start,
                        '$lte': today_end
                    }
                }).sort('time', 1))

                # 获取老年人的近期提醒
                elder_reminders = list(mongo.db.reminders.find({
                    'user_id': ObjectId(user['elder_id']),
                    'completed': False
                }).sort('date', 1).limit(5))

                # 获取老年人的财务摘要
                finance_summary = {
                    'total_monthly_expenses': 0,
                    'total_paid_expenses': 0,
                    'pending_fixed_total': 0
                }

                # 计算常规支出
                regular_expenses = list(mongo.db.regular_expenses.find({
                    'user_id': ObjectId(user['elder_id']),
                    'date': {
                        '$gte': today_start,
                        '$lte': today_end
                    }
                }))

                finance_summary['total_monthly_expenses'] = sum(expense['amount'] for expense in regular_expenses)

                # 计算固定支出
                fixed_expenses = list(mongo.db.fixed_expenses.find({
                    'user_id': ObjectId(user['elder_id'])
                }))

                paid_fixed = sum(expense['amount'] for expense in fixed_expenses if expense.get('is_paid', False))
                total_fixed = sum(expense['amount'] for expense in fixed_expenses)

                finance_summary['total_paid_expenses'] = finance_summary['total_monthly_expenses'] + paid_fixed
                finance_summary['pending_fixed_total'] = total_fixed - paid_fixed

                # 获取关联老年人最近一小时的紧急日志
                one_hour_ago = datetime.utcnow() - timedelta(hours=1)
                emergency_logs = list(mongo.db.emergency_logs.find({
                    'user_id': ObjectId(user['elder_id']),
                    'created_at': {'$gte': one_hour_ago}
                }).sort('created_at', -1))

                # 清理旧的紧急日志
                cleanup_old_emergency_logs()

                return render_template('child_dashboard.html',
                                       user=user,
                                       linked_elder=linked_elder,
                                       elder_events=elder_events,
                                       today_medicines=today_medicines,
                                       elder_reminders=elder_reminders,
                                       finance_summary=finance_summary,
                                       emergency_logs=emergency_logs)

        return render_template('child_dashboard.html',
                               user=user,
                               linked_elder=None)

    except Exception as e:
        print(f"子女主界面路由错误: {str(e)}")
        flash('加载主界面时出错', 'error')
        return redirect(url_for('index'))


def cleanup_old_emergency_logs():
    """清理超过一小时的紧急日志"""
    try:
        one_hour_ago = datetime.utcnow() - timedelta(hours=1)
        result = mongo.db.emergency_logs.delete_many({
            'created_at': {'$lt': one_hour_ago}
        })
        if result.deleted_count > 0:
            print(f"清理了 {result.deleted_count} 条旧的紧急日志")
    except Exception as e:
        print(f"清理紧急日志时出错: {str(e)}")


@app.route('/social_events')
@login_required
def social_events():
    try:
        print("\n=== 社交活动调试 ===")
        user_id = ObjectId(session['user_id'])
        print(f"为用户ID获取活动: {user_id}")

        # 验证MongoDB连接
        try:
            mongo.db.command('ping')
            print("MongoDB连接正常")
        except Exception as e:
            print(f"MongoDB连接错误: {str(e)}")
            flash('数据库连接错误。请重试。', 'error')
            return redirect(url_for('dashboard'))

        # 获取所有活动，按时间排序
        events = list(mongo.db.events.find().sort('datetime', 1))
        print(f"找到的活动总数: {len(events)}")

        # 获取当前用户组织的活动
        my_events = list(mongo.db.events.find({
            'organizer_id': user_id
        }).sort('datetime', 1))
        print(f"用户组织的活动数: {len(my_events)}")

        # 获取用户参与的活动
        user_participating_events = []
        for event in events:
            if user_id in event.get('participants', []):
                user_participating_events.append(str(event['_id']))
        print(f"用户参与的活动数: {len(user_participating_events)}")

        # 获取此页面的任何待处理通知
        notifications = session.pop('social_events_notifications', [])

        return render_template('social_events.html',
                               events=events,
                               my_events=my_events,
                               user_participating_events=user_participating_events,
                               notifications=notifications)
    except Exception as e:
        print(f"\n社交活动路由错误: {str(e)}")
        print(f"错误类型: {type(e)}")
        import traceback
        print(f"追踪: {traceback.format_exc()}")
        flash('加载活动时出错。请重试。', 'error')
        return redirect(url_for('dashboard'))


@app.route('/create_event', methods=['POST'])
@login_required
def create_event():
    try:
        print("\n=== 活动创建调试 ===")
        print("收到的表单数据:", dict(request.form))

        # 验证必填字段
        required_fields = ['eventName', 'eventDescription', 'eventDate', 'eventTime', 'location']
        for field in required_fields:
            if not request.form.get(field):
                print(f"缺少必填字段: {field}")
                session['social_events_notifications'] = [{'type': 'error', 'message': f'缺少必填字段: {field}'}]
                return redirect(url_for('social_events'))

        # 解析日期和时间
        try:
            event_date = datetime.strptime(request.form.get('eventDate'), '%Y-%m-%d')
            event_time = datetime.strptime(request.form.get('eventTime'), '%H:%M').time()
            event_datetime = datetime.combine(event_date.date(), event_time)
            print(f"解析的日期时间: {event_datetime}")
        except ValueError as e:
            print(f"日期/时间解析错误: {str(e)}")
            session['social_events_notifications'] = [{'type': 'error', 'message': '无效的日期或时间格式'}]
            return redirect(url_for('social_events'))

        # 验证活动日期（从现在起2到7天内）
        today = datetime.now().date()
        min_date = today + timedelta(days=2)
        max_date = today + timedelta(days=7)

        if not (min_date <= event_date.date() <= max_date):
            print(f"无效日期: {event_date.date()}。必须在 {min_date} 和 {max_date} 之间")
            session['social_events_notifications'] = [{'type': 'error', 'message': '活动日期必须在从现在起2到7天内。'}]
            return redirect(url_for('social_events'))

        # 验证活动时间（早上5:00到晚上10:00之间）
        if not (5 <= event_time.hour < 22 or (event_time.hour == 22 and event_time.minute == 0)):
            print(f"无效时间: {event_time}")
            session['social_events_notifications'] = [
                {'type': 'error', 'message': '活动时间必须在早上5:00到晚上10:00之间。'}]
            return redirect(url_for('social_events'))

        # 获取组织者详情
        user_id = ObjectId(session['user_id'])
        organizer = mongo.db.users.find_one({'_id': user_id})
        if not organizer:
            print(f"未找到用户ID的组织者: {session['user_id']}")
            session['social_events_notifications'] = [{'type': 'error', 'message': '未找到用户！'}]
            return redirect(url_for('social_events'))

        # 创建活动文档
        event = {
            'name': request.form.get('eventName'),
            'description': request.form.get('eventDescription'),
            'datetime': event_datetime,
            'location': request.form.get('location'),
            'max_participants': int(request.form.get('maxParticipants', 1)),
            'organizer_id': user_id,
            'organizer_name': organizer['name'],
            'participants': [user_id],
            'created_at': datetime.now(timezone.utc)
        }

        # 插入活动
        try:
            result = mongo.db.events.insert_one(event)
            print(f"\n插入结果: {result.inserted_id}")

            if result.inserted_id:
                # 验证活动是否已插入
                inserted_event = mongo.db.events.find_one({'_id': result.inserted_id})
                if inserted_event:
                    print("活动成功在数据库中验证")
                    session['social_events_notifications'] = [{'type': 'success', 'message': '活动创建成功！'}]
                else:
                    print("插入后未找到活动！")
                    session['social_events_notifications'] = [
                        {'type': 'error', 'message': '验证活动创建时出错。请重试。'}]
            else:
                print("创建活动失败 - 未返回inserted_id")
                session['social_events_notifications'] = [{'type': 'error', 'message': '创建活动时出错。请重试。'}]

        except Exception as e:
            print(f"\n数据库插入错误: {str(e)}")
            print(f"错误类型: {type(e)}")
            import traceback
            print(f"追踪: {traceback.format_exc()}")
            session['social_events_notifications'] = [{'type': 'error', 'message': '创建活动时出错。请重试。'}]

    except Exception as e:
        print(f"\n常规错误: {str(e)}")
        print(f"错误类型: {type(e)}")
        import traceback
        print(f"追踪: {traceback.format_exc()}")
        session['social_events_notifications'] = [{'type': 'error', 'message': '创建活动时出错。请重试。'}]

    print("\n=== 活动创建调试结束 ===\n")
    return redirect(url_for('social_events'))


@app.route('/event/<event_id>')
@login_required
def view_event(event_id):
    try:
        event = mongo.db.events.find_one({'_id': ObjectId(event_id)})
        if not event:
            flash('未找到活动！', 'error')
            return redirect(url_for('social_events'))

        # 获取参与者的详细信息及加入时间
        participants = []
        for participant_id in event['participants']:
            user = mongo.db.users.find_one({'_id': participant_id})
            if user:
                # 从活动的参与者数组中获取加入时间
                join_time = event.get('participant_join_times', {}).get(str(participant_id), event['created_at'])
                participants.append({
                    'name': user['name'],
                    'email': user['email'],
                    'join_time': join_time
                })

        # 按加入时间排序参与者
        participants.sort(key=lambda x: x['join_time'], reverse=True)

        # 检查当前用户是否参与
        is_participating = ObjectId(session['user_id']) in event['participants']

        return render_template('view_event.html',
                               event=event,
                               participants=participants,
                               is_participating=is_participating)
    except Exception as e:
        print(f"查看活动时出错: {str(e)}")
        flash('查看活动时出错！', 'error')
        return redirect(url_for('social_events'))


@app.route('/event/join/<event_id>', methods=['POST'])
@login_required
def join_event(event_id):
    try:
        user_id = ObjectId(session['user_id'])
        event = mongo.db.events.find_one({'_id': ObjectId(event_id)})
        if not event:
            return jsonify({'success': False, 'message': '未找到活动'})

        if len(event['participants']) >= event['max_participants']:
            return jsonify({'success': False, 'message': '活动已满员'})

        if user_id in event['participants']:
            return jsonify({'success': False, 'message': '您已经参加了此活动'})

        # 添加参与者及加入时间
        current_time = datetime.now(timezone.utc)
        mongo.db.events.update_one(
            {'_id': ObjectId(event_id)},
            {
                '$push': {'participants': user_id},
                '$set': {f'participant_join_times.{str(user_id)}': current_time}
            }
        )
        session['social_events_notifications'] = [{'type': 'success', 'message': '成功参加活动！'}]
        return jsonify({'success': True})
    except Exception as e:
        print(f"参加活动时出错: {str(e)}")
        return jsonify({'success': False, 'message': '参加活动时出错'})


@app.route('/event/leave/<event_id>', methods=['POST'])
@login_required
def leave_event(event_id):
    try:
        user_id = ObjectId(session['user_id'])
        event = mongo.db.events.find_one({'_id': ObjectId(event_id)})

        if not event:
            return jsonify({'success': False, 'message': '未找到活动'})

        # 检查用户是否是组织者
        if event['organizer_id'] == user_id:
            return jsonify({'success': False, 'message': '活动组织者不能离开自己的活动。请删除活动。'})

        mongo.db.events.update_one(
            {'_id': ObjectId(event_id)},
            {
                '$pull': {'participants': user_id},
                '$unset': {f'participant_join_times.{str(user_id)}': ""}
            }
        )
        session['social_events_notifications'] = [{'type': 'success', 'message': '成功退出活动'}]
        return jsonify({'success': True})
    except Exception as e:
        print(f"退出活动时出错: {str(e)}")
        return jsonify({'success': False, 'message': '退出活动时出错'})


@app.route('/event/delete/<event_id>', methods=['POST'])
@login_required
def delete_event(event_id):
    try:
        user_id = ObjectId(session['user_id'])
        event = mongo.db.events.find_one({'_id': ObjectId(event_id)})

        if not event:
            return jsonify({'success': False, 'message': '未找到活动'})

        # 检查用户是否是组织者
        if event['organizer_id'] != user_id:
            return jsonify({'success': False, 'message': '只有活动组织者可以删除活动'})

        # 删除活动
        result = mongo.db.events.delete_one({'_id': ObjectId(event_id)})

        if result.deleted_count > 0:
            session['social_events_notifications'] = [{'type': 'success', 'message': '活动删除成功'}]
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'message': '删除活动时出错'})

    except Exception as e:
        print(f"删除活动时出错: {str(e)}")
        return jsonify({'success': False, 'message': '删除活动时出错'})


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        # 获取表单数据
        name = request.form.get('name')
        email = request.form.get('email')
        phone = request.form.get('phone')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        role = request.form.get('role')
        gender = request.form.get('gender')
        age = request.form.get('age')
        parent_email = request.form.get('parent_email')

        # 地址信息
        address = request.form.get('address')
        city = request.form.get('city')
        state = request.form.get('state')
        pincode = request.form.get('pincode')
        emergency_contact = request.form.get('emergency_contact')

        # 验证必填字段
        if not all([name, email, phone, password, confirm_password, role, gender, age, address, city, state, pincode]):
            flash('请填写所有必填字段', 'error')
            return redirect(url_for('register'))

        # 验证邮箱格式
        if not '@' in email or not '.' in email:
            flash('请输入有效的邮箱地址', 'error')
            return redirect(url_for('register'))

        # 验证手机号码
        if not phone.isdigit() or len(phone) != 11:
            flash('请输入有效的11位手机号码', 'error')
            return redirect(url_for('register'))

        # 验证邮政编码
        if not pincode.isdigit() or len(pincode) != 6:
            flash('请输入有效的6位邮政编码', 'error')
            return redirect(url_for('register'))

        # 验证年龄
        try:
            age = int(age)
            if age < 0 or age > 120:
                flash('请输入有效的年龄（0-120）', 'error')
                return redirect(url_for('register'))
        except ValueError:
            flash('请输入有效的年龄', 'error')
            return redirect(url_for('register'))

        # 验证密码
        if password != confirm_password:
            flash('密码不匹配', 'error')
            return redirect(url_for('register'))

        if len(password) < 6:
            flash('密码长度至少6位', 'error')
            return redirect(url_for('register'))

        # 检查邮箱是否已存在
        if mongo.db.users.find_one({'email': email}):
            flash('邮箱已被注册！', 'error')
            return redirect(url_for('register'))

        # 如果注册为家庭成员，验证父母邮箱
        elder_id = None
        if role == 'child':
            if not parent_email:
                flash('家庭成员需要提供关联老年人邮箱', 'error')
                return redirect(url_for('register'))

            elder = mongo.db.users.find_one({'email': parent_email, 'role': 'elder'})
            if not elder:
                flash('未找到关联老年人邮箱或该邮箱未注册为老年人！', 'error')
                return redirect(url_for('register'))
            elder_id = str(elder['_id'])

        # 创建新用户
        user = {
            'name': name,
            'email': email,
            'phone': phone,
            'password_hash': generate_password_hash(password),
            'role': role,
            'gender': gender,
            'age': age,
            'elder_id': elder_id,
            'address': {
                'street': address,
                'city': city,
                'state': state,
                'pincode': pincode
            },
            'emergency_contact': emergency_contact,
            'created_at': datetime.utcnow()
        }

        try:
            result = mongo.db.users.insert_one(user)
            flash('注册成功！请登录', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            print(f"注册过程中出错: {str(e)}")
            flash('注册过程中出错。请重试。', 'error')
            return redirect(url_for('register'))

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')

        # 检查管理员登录
        if email == 'admin@agewell.in' and password == 'admin@1':
            session['user_id'] = 'admin'
            session['is_admin'] = True
            session['role'] = 'admin'
            flash('欢迎管理员！', 'success')
            return redirect(url_for('admin_dashboard'))

        # 普通用户登录
        user = mongo.db.users.find_one({'email': email})
        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = str(user['_id'])
            session['is_admin'] = False
            session['role'] = user['role']
            flash('登录成功！', 'success')
            return redirect(url_for('index'))

        flash('无效的邮箱或密码！', 'error')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.pop('user_id', None)
    flash('您已退出登录。', 'info')
    return redirect(url_for('index'))


@app.route('/profile')
@login_required
def profile():
    try:
        user = mongo.db.users.find_one({'_id': ObjectId(session['user_id'])})
        if not user:
            flash('未找到用户！', 'error')
            return redirect(url_for('dashboard'))

        linked_elder = None
        linked_children = None

        if user['role'] == 'child' and user.get('elder_id'):
            linked_elder = mongo.db.users.find_one({'_id': ObjectId(user['elder_id'])})
        elif user['role'] == 'elder':
            linked_children = list(mongo.db.users.find({'elder_id': str(user['_id'])}))

        # 获取此页面的任何待处理通知
        notifications = session.pop('profile_notifications', [])

        return render_template('profile.html',
                               user=user,
                               linked_elder=linked_elder,
                               linked_children=linked_children,
                               notifications=notifications)
    except Exception as e:
        print(f"个人资料路由错误: {str(e)}")
        flash('加载个人资料时出错！', 'error')
        return redirect(url_for('dashboard'))


@app.route('/update_profile', methods=['POST'])
@login_required
def update_profile():
    try:
        user_id = ObjectId(session['user_id'])
        user = mongo.db.users.find_one({'_id': user_id})

        if not user:
            session['profile_notifications'] = [{'type': 'error', 'message': '未找到用户！'}]
            return redirect(url_for('profile'))

        # 获取表单数据
        name = request.form.get('name')
        phone = request.form.get('phone')
        age = request.form.get('age')
        gender = request.form.get('gender')

        # 验证必填字段
        if not name or not phone:
            session['profile_notifications'] = [{'type': 'error', 'message': '姓名和手机号码是必填项！'}]
            return redirect(url_for('profile'))

        # 验证手机号码（基本验证）
        if not phone.isdigit() or len(phone) < 10:
            session['profile_notifications'] = [{'type': 'error', 'message': '请输入有效的手机号码！'}]
            return redirect(url_for('profile'))

        # 验证年龄（如果提供）
        if age:
            try:
                age = int(age)
                if age < 0 or age > 120:
                    session['profile_notifications'] = [{'type': 'error', 'message': '请输入有效的年龄！'}]
                    return redirect(url_for('profile'))
            except ValueError:
                session['profile_notifications'] = [{'type': 'error', 'message': '请输入有效的年龄！'}]
                return redirect(url_for('profile'))

        # 准备更新数据
        update_data = {
            'name': name,
            'phone': phone,
            'gender': gender
        }

        if age:
            update_data['age'] = age

        # 处理密码更改（如果提供）
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')

        if current_password and new_password and confirm_password:
            # 验证当前密码
            if not check_password_hash(user['password_hash'], current_password):
                session['profile_notifications'] = [{'type': 'error', 'message': '当前密码不正确！'}]
                return redirect(url_for('profile'))

            # 验证新密码
            if new_password != confirm_password:
                session['profile_notifications'] = [{'type': 'error', 'message': '新密码不匹配！'}]
                return redirect(url_for('profile'))

            if len(new_password) < 6:
                session['profile_notifications'] = [{'type': 'error', 'message': '密码长度至少6位！'}]
                return redirect(url_for('profile'))

            # 更新密码
            update_data['password_hash'] = generate_password_hash(new_password)

        # 更新用户个人资料
        try:
            result = mongo.db.users.update_one(
                {'_id': user_id},
                {'$set': update_data}
            )

            if result.modified_count > 0:
                session['profile_notifications'] = [{'type': 'success', 'message': '个人资料更新成功！'}]
            else:
                session['profile_notifications'] = [{'type': 'info', 'message': '您的个人资料没有更改。'}]

        except Exception as e:
            print(f"更新个人资料时出错: {str(e)}")
            session['profile_notifications'] = [{'type': 'error', 'message': '更新个人资料时出错。请重试。'}]

    except Exception as e:
        print(f"更新个人资料路由错误: {str(e)}")
        session['profile_notifications'] = [{'type': 'error', 'message': '更新个人资料时出错。请重试。'}]

    return redirect(url_for('profile'))


@app.route('/admin-dashboard')
@login_required
def admin_dashboard():
    try:
        # 检查用户是否是管理员
        if not session.get('is_admin'):
            flash('访问被拒绝', 'error')
            return redirect(url_for('index'))

        # 获取所有用户
        users = list(mongo.db.users.find())

        # 获取所有活动
        events = list(mongo.db.events.find().sort('datetime', 1))

        # 获取所有药品计划
        medicine_schedules = list(mongo.db.medicine_schedule.find().sort('date', 1))

        # 获取所有提醒
        reminders = list(mongo.db.reminders.find().sort('date', 1))

        # 获取所有常规支出
        regular_expenses = list(mongo.db.regular_expenses.find().sort('date', 1))

        # 获取所有固定支出
        fixed_expenses = list(mongo.db.fixed_expenses.find())

        # 获取所有反馈项
        feedback_items = list(mongo.db.feedback.find().sort('created_at', -1))

        # 获取所有教程请求
        tutorial_requests = list(mongo.db.tutorial_requests.find().sort('created_at', -1))

        # 获取最近一小时的紧急日志
        one_hour_ago = datetime.utcnow() - timedelta(hours=1)
        emergency_logs = list(mongo.db.emergency_logs.find({
            'created_at': {'$gte': one_hour_ago}
        }).sort('created_at', -1))

        # 清理旧的紧急日志
        cleanup_old_emergency_logs()

        return render_template('admin_dashboard.html',
                               users=users,
                               events=events,
                               medicine_schedules=medicine_schedules,
                               reminders=reminders,
                               regular_expenses=regular_expenses,
                               fixed_expenses=fixed_expenses,
                               feedback_items=feedback_items,
                               tutorial_requests=tutorial_requests,
                               emergency_logs=emergency_logs)

    except Exception as e:
        print(f"管理员主界面路由错误: {str(e)}")
        flash('加载主界面时出错', 'error')
        return redirect(url_for('index'))


@app.route('/admin/user/<user_id>')
@login_required
def admin_user_details(user_id):
    if not session.get('is_admin'):
        flash('访问被拒绝。需要管理员权限。', 'error')
        return redirect(url_for('admin_dashboard'))

    try:
        # 获取用户详情
        user = mongo.db.users.find_one({'_id': ObjectId(user_id)})
        if not user:
            flash('未找到用户', 'error')
            return redirect(url_for('admin_dashboard'))

        # 如果用户是老年人，获取关联子女
        linked_children = []
        if user['role'] == 'elder':
            linked_children = list(mongo.db.users.find({'elder_id': str(user['_id'])}))

        # 如果用户是子女，获取关联老年人
        linked_elder = None
        if user['role'] == 'child' and user.get('elder_id'):
            linked_elder = mongo.db.users.find_one({'_id': ObjectId(user['elder_id'])})

        return render_template('admin_user_details.html',
                               user=user,
                               linked_children=linked_children,
                               linked_elder=linked_elder)
    except Exception as e:
        print(f"管理员用户详情错误: {str(e)}")
        flash('加载用户详情时出错', 'error')
        return redirect(url_for('admin_dashboard'))


@app.route('/admin/feedback/update/<feedback_id>', methods=['POST'])
def admin_update_feedback(feedback_id):
    if not session.get('is_admin'):
        return jsonify({'success': False, 'message': '访问被拒绝'})

    try:
        status = request.form.get('status')
        if status not in ['pending', 'resolved']:
            return jsonify({'success': False, 'message': '无效的状态'})

        result = mongo.db.feedback.update_one(
            {'_id': ObjectId(feedback_id)},
            {'$set': {'status': status}}
        )

        if result.modified_count > 0:
            return jsonify({'success': True})
        return jsonify({'success': False, 'message': '更新状态失败'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/admin/feedback/delete/<feedback_id>', methods=['POST'])
def admin_delete_feedback(feedback_id):
    if not session.get('is_admin'):
        return jsonify({'success': False, 'message': '访问被拒绝'})

    try:
        result = mongo.db.feedback.delete_one({'_id': ObjectId(feedback_id)})
        if result.deleted_count > 0:
            return jsonify({'success': True})
        return jsonify({'success': False, 'message': '删除反馈失败'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


# 添加用户提交反馈/投诉/请求的路由
@app.route('/submit_feedback', methods=['POST'])
@login_required
def submit_feedback():
    try:
        # 获取表单数据
        feedback_type = request.form.get('type')
        rating = request.form.get('rating')
        message = request.form.get('message')
        priority = request.form.get('priority')

        # 处理文件上传
        file_path = None
        if 'file' in request.files:
            file = request.files['file']
            if file and file.filename:
                # 创建上传目录（如果不存在）
                upload_dir = os.path.join(app.static_folder, 'uploads', 'feedback')
                os.makedirs(upload_dir, exist_ok=True)

                # 生成唯一文件名
                filename = secure_filename(file.filename)
                unique_filename = f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{filename}"
                file_path = os.path.join('uploads', 'feedback', unique_filename)

                # 保存文件
                file.save(os.path.join(app.static_folder, file_path))

        # 创建反馈条目
        feedback = {
            'user_id': ObjectId(session['user_id']),
            'type': feedback_type,
            'rating': int(rating) if rating else None,  # 将评分转换为整数
            'message': message,
            'priority': priority,
            'file_path': file_path,
            'status': 'pending',
            'created_at': datetime.utcnow()
        }

        # 调试打印
        print(f"调试 - 提交反馈: {feedback}")

        result = mongo.db.feedback.insert_one(feedback)
        if result.inserted_id:
            return jsonify({
                'success': True,
                'message': '反馈提交成功'
            })
        return jsonify({
            'success': False,
            'message': '提交反馈失败'
        }), 500
    except Exception as e:
        print(f"提交反馈时出错: {str(e)}")
        return jsonify({
            'success': False,
            'message': '提交反馈时出错'
        }), 500


@app.route('/admin/feedback')
@login_required
def admin_feedback():
    if not session.get('is_admin'):
        flash('访问被拒绝。需要管理员权限。', 'error')
        return redirect(url_for('dashboard'))

    try:
        # 获取所有反馈及用户详情
        feedback_list = list(mongo.db.feedback.find().sort('created_at', -1))

        # 为每个反馈添加用户详情
        for feedback in feedback_list:
            user = mongo.db.users.find_one({'_id': feedback['user_id']})
            feedback['user_name'] = user['name'] if user else '未知用户'

            # 确保包含评分
            if 'rating' not in feedback:
                feedback['rating'] = None
            else:
                feedback['rating'] = int(feedback['rating'])

            # 将ObjectId转换为字符串以便JSON序列化
            feedback['_id'] = str(feedback['_id'])
            feedback['user_id'] = str(feedback['user_id'])

            # 格式化日期时间
            if isinstance(feedback.get('created_at'), datetime):
                feedback['created_at'] = feedback['created_at'].strftime('%Y-%m-%d %H:%M')

        print(f"调试 - 第一个反馈项: {feedback_list[0] if feedback_list else '未找到反馈'}")

        return render_template('admin/feedback.html', feedback_list=feedback_list)
    except Exception as e:
        print(f"管理员反馈错误: {str(e)}")
        flash('加载反馈时出错', 'error')
        return redirect(url_for('admin_dashboard'))


@app.route('/admin/feedback/<feedback_id>/update', methods=['POST'])
@login_required
def update_feedback_status(feedback_id):
    if not session.get('is_admin'):
        return jsonify({'success': False, 'message': '访问被拒绝'}), 403

    try:
        feedback = mongo.db.feedback.find_one({'_id': ObjectId(feedback_id)})
        if not feedback:
            return jsonify({'success': False, 'message': '未找到反馈'}), 404

        status = request.form.get('status')
        if status not in ['pending', 'in_progress', 'resolved']:
            return jsonify({'success': False, 'message': '无效的状态'}), 400

        result = mongo.db.feedback.update_one(
            {'_id': ObjectId(feedback_id)},
            {'$set': {'status': status}}
        )

        if result.modified_count > 0:
            return jsonify({
                'success': True,
                'message': '反馈状态更新成功'
            })
        return jsonify({
            'success': False,
            'message': '反馈没有更改'
        }), 200
    except Exception as e:
        app.logger.error(f"更新反馈状态时出错: {str(e)}")
        return jsonify({
            'success': False,
            'message': '更新反馈状态时出错'
        }), 500


@app.route('/learning-corner')
@login_required
def learning_corner():
    user = mongo.db.users.find_one({'_id': ObjectId(session['user_id'])})

    # 获取用户的教程请求
    user_requests = list(mongo.db.tutorial_requests.find(
        {'user_id': ObjectId(session['user_id'])}
    ).sort('created_at', -1))

    return render_template('learning_corner.html',
                           user=user,
                           user_requests=user_requests)


@app.route('/learning-corner/whatsapp')
@login_required
def whatsapp_guide():
    user = mongo.db.users.find_one({'_id': ObjectId(session['user_id'])})
    return render_template('guides/whatsapp_guide.html', user=user)


@app.route('/learning-corner/youtube')
@login_required
def youtube_guide():
    user = mongo.db.users.find_one({'_id': ObjectId(session['user_id'])})
    return render_template('guides/youtube_guide.html', user=user)


@app.route('/learning-corner/payments')
@login_required
def payments_guide():
    user = mongo.db.users.find_one({'_id': ObjectId(session['user_id'])})
    return render_template('guides/payments_guide.html', user=user)


@app.route('/learning-corner/social-media')
@login_required
def social_media_guide():
    user = mongo.db.users.find_one({'_id': ObjectId(session['user_id'])})
    return render_template('guides/social_media_guide.html', user=user)


@app.route('/learning-corner/smartphone')
@login_required
def smartphone_guide():
    user = mongo.db.users.find_one({'_id': ObjectId(session['user_id'])})
    return render_template('guides/smartphone_guide.html', user=user)


@app.route('/learning-corner/video-calls')
@login_required
def video_calls_guide():
    user = mongo.db.users.find_one({'_id': ObjectId(session['user_id'])})
    return render_template('guides/video_calls_guide.html', user=user)


@app.route('/submit_tutorial_request', methods=['POST'])
@login_required
def submit_tutorial_request():
    try:
        # 获取表单数据
        topic = request.form.get('topic')
        category = request.form.get('category')
        description = request.form.get('description')
        difficulty = request.form.get('difficulty')
        platform = request.form.get('platform')
        additional_notes = request.form.get('additional_notes')

        # 获取用户详情
        user = mongo.db.users.find_one({'_id': ObjectId(session['user_id'])})
        if not user:
            return jsonify({'success': False, 'message': '未找到用户'}), 404

        # 创建新的教程请求
        new_request = {
            'user_id': ObjectId(session['user_id']),
            'user_name': user['name'],
            'topic': topic,
            'category': category,
            'description': description,
            'difficulty': difficulty,
            'platform': platform,
            'additional_notes': additional_notes,
            'status': 'pending',
            'created_at': datetime.utcnow()
        }

        # 添加到数据库
        result = mongo.db.tutorial_requests.insert_one(new_request)
        if result.inserted_id:
            return jsonify({'success': True, 'message': '教程请求提交成功'})
        return jsonify({'success': False, 'message': '提交教程请求失败'}), 500
    except Exception as e:
        print(f"提交教程请求时出错: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/admin/debug/tutorial_requests')
@login_required
def debug_tutorial_requests():
    if not session.get('is_admin'):
        return jsonify({'error': '未授权'}), 403

    try:
        # 从MongoDB获取原始数据
        requests = list(mongo.db.tutorial_requests.find())

        # 将ObjectId转换为字符串以便JSON序列化
        for req in requests:
            req['_id'] = str(req['_id'])
            if 'user_id' in req:
                req['user_id'] = str(req['user_id'])
            if isinstance(req.get('created_at'), datetime):
                req['created_at'] = req['created_at'].strftime('%Y-%m-%d %H:%M')

        return jsonify({
            'count': len(requests),
            'requests': requests
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/admin/tutorial_requests')
@login_required
def admin_tutorial_requests():
    if not session.get('is_admin'):
        flash('访问被拒绝。需要管理员权限。', 'error')
        return redirect(url_for('login'))

    try:
        # 获取所有教程请求
        requests = list(mongo.db.tutorial_requests.find())
        print(f"\n在数据库中找到 {len(requests)} 个教程请求")

        # 格式化请求以供显示
        formatted_requests = []
        for req in requests:
            # 将ObjectId转换为字符串
            req['_id'] = str(req['_id'])
            if 'user_id' in req:
                req['user_id'] = str(req['user_id'])

            # 格式化日期时间
            if isinstance(req.get('created_at'), datetime):
                req['created_at'] = req['created_at'].strftime('%Y-%m-%d %H:%M')

            formatted_requests.append(req)

        print(f"格式化 {len(formatted_requests)} 个请求以供显示")

        # 调试输出
        print("\n第一个请求数据:")
        if formatted_requests:
            print(formatted_requests[0])

        return render_template('admin/tutorial_requests.html', requests=formatted_requests)
    except Exception as e:
        print(f"管理员教程请求错误: {str(e)}")
        flash('加载教程请求时出错', 'error')
        return redirect(url_for('admin_dashboard'))


@app.route('/admin/tutorial_request/<request_id>/update', methods=['POST'])
@login_required
def update_tutorial_request(request_id):
    if not session.get('is_admin'):
        return jsonify({'success': False, 'message': '访问被拒绝'})

    try:
        data = request.get_json()
        tutorial_request = mongo.db.tutorial_requests.find_one({'_id': ObjectId(request_id)})

        if not tutorial_request:
            return jsonify({'success': False, 'message': '未找到请求'}), 404

        update_data = {
            'status': data.get('status', tutorial_request['status']),
            'admin_notes': data.get('admin_notes', tutorial_request.get('admin_notes', '')),
            'updated_at': datetime.utcnow()
        }

        result = mongo.db.tutorial_requests.update_one(
            {'_id': ObjectId(request_id)},
            {'$set': update_data}
        )

        if result.modified_count > 0:
            return jsonify({'success': True})
        return jsonify({'success': False, 'message': '请求没有更改'}), 200
    except Exception as e:
        print(f"更新教程请求时出错: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/finance-management')
@login_required
def finance_management():
    try:
        user_id = ObjectId(session['user_id'])

        # 获取当前月份的日期
        today = datetime.now()
        first_day = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_day = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        days_in_month = (last_day - first_day).days + 1

        # 获取当前月份的常规支出
        regular_expenses = list(mongo.db.regular_expenses.find({
            'user_id': user_id,
            'date': {
                '$gte': first_day,
                '$lte': last_day
            }
        }).sort('date', -1))

        # 计算常规支出总额
        regular_total = sum(expense['amount'] for expense in regular_expenses)
        paid_regular_total = sum(expense['amount'] for expense in regular_expenses)
        pending_regular_total = 0  # 所有常规支出视为已支付
        daily_regular_average = round(regular_total / days_in_month, 2)

        # 获取固定支出
        fixed_expenses = list(mongo.db.fixed_expenses.find({
            'user_id': user_id
        }).sort('date', 1))

        # 计算固定支出总额
        fixed_total = sum(expense['amount'] for expense in fixed_expenses if expense['frequency'] == 'monthly')
        paid_fixed_total = sum(expense['amount'] for expense in fixed_expenses if expense.get('is_paid', False))
        pending_fixed_total = fixed_total - paid_fixed_total
        daily_fixed_average = round(fixed_total / days_in_month, 2)

        # 计算月度总支出
        total_monthly_expenses = regular_total + fixed_total
        total_paid_expenses = paid_regular_total + paid_fixed_total

        # 获取最近支付的常规支出（最近5条）
        paid_regular_expenses = list(mongo.db.regular_expenses.find({
            'user_id': user_id,
            'date': {
                '$gte': first_day,
                '$lte': last_day
            }
        }).sort('date', -1).limit(5))

        # 获取最近支付的固定支出（最近5条）
        paid_fixed_expenses = list(mongo.db.fixed_expenses.find({
            'user_id': user_id,
            'is_paid': True
        }).sort('paid_at', -1).limit(5))

        return render_template('finance_management.html',
                               regular_total=regular_total,
                               fixed_total=fixed_total,
                               paid_regular_total=paid_regular_total,
                               paid_fixed_total=paid_fixed_total,
                               pending_regular_total=pending_regular_total,
                               pending_fixed_total=pending_fixed_total,
                               daily_regular_average=daily_regular_average,
                               daily_fixed_average=daily_fixed_average,
                               total_monthly_expenses=total_monthly_expenses,
                               total_paid_expenses=total_paid_expenses,
                               paid_regular_expenses=paid_regular_expenses,
                               paid_fixed_expenses=paid_fixed_expenses)
    except Exception as e:
        print(f"财务管理错误: {str(e)}")
        flash('加载财务管理页面时出错', 'error')
        return redirect(url_for('dashboard'))


@app.route('/regular-expenses')
@login_required
def regular_expenses():
    try:
        user_id = ObjectId(session['user_id'])

        # 获取当前月份的支出
        today = datetime.now()
        first_day = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_day = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)

        # 获取当前月份的所有常规支出
        expenses = list(mongo.db.regular_expenses.find({
            'user_id': user_id,
            'date': {
                '$gte': first_day,
                '$lte': last_day
            }
        }).sort('date', -1))

        # 计算月度总额
        monthly_total = sum(expense['amount'] for expense in expenses)

        # 计算日均支出
        days_in_month = (last_day - first_day).days + 1
        daily_average = round(monthly_total / days_in_month, 2)

        # 计算分类总额用于饼图
        category_totals = {}
        for expense in expenses:
            category = expense['category']
            if category not in category_totals:
                category_totals[category] = 0
            category_totals[category] += expense['amount']

        # 将分类总额转换为图表所需的列表格式
        category_data = [
            {'category': category, 'amount': amount}
            for category, amount in category_totals.items()
        ]

        # 按金额排序分类（从高到低）
        category_data.sort(key=lambda x: x['amount'], reverse=True)

        # 找出最高支出分类
        highest_category = max(category_totals.items(), key=lambda x: x[1])[0] if category_totals else "无支出"

        # 获取用户的月度预算
        user = mongo.db.users.find_one({'_id': user_id})
        monthly_budget = user.get('monthly_budget', 0)
        remaining_budget = monthly_budget - monthly_total

        return render_template('regular_expenses.html',
                               expenses=expenses,
                               monthly_total=monthly_total,
                               daily_average=daily_average,
                               highest_category=highest_category,
                               remaining_budget=remaining_budget,
                               category_data=category_data,
                               total_amount=monthly_total)
    except Exception as e:
        print(f"常规支出错误: {str(e)}")
        flash('加载支出时出错', 'error')
        return redirect(url_for('finance_management'))


@app.route('/add-regular-expense', methods=['POST'])
@login_required
def add_regular_expense():
    try:
        user_id = ObjectId(session['user_id'])

        # 获取表单数据
        name = request.form.get('expenseName')
        amount = float(request.form.get('expenseAmount'))
        category = request.form.get('expenseCategory')
        description = request.form.get('expenseDescription')
        date = datetime.strptime(request.form.get('expenseDate'), '%Y-%m-%d')

        # 创建支出文档
        expense = {
            'user_id': user_id,
            'name': name,
            'amount': amount,
            'category': category,
            'description': description,
            'date': date,
            'created_at': datetime.utcnow()
        }

        # 插入数据库
        result = mongo.db.regular_expenses.insert_one(expense)

        if result.inserted_id:
            flash('支出添加成功！', 'success')
        else:
            flash('添加支出时出错', 'error')

    except Exception as e:
        print(f"添加支出时出错: {str(e)}")
        flash('添加支出时出错', 'error')

    return redirect(url_for('regular_expenses'))


@app.route('/delete-expense/<expense_id>', methods=['POST'])
@login_required
def delete_expense(expense_id):
    try:
        user_id = ObjectId(session['user_id'])

        # 删除支出
        result = mongo.db.regular_expenses.delete_one({
            '_id': ObjectId(expense_id),
            'user_id': user_id
        })

        if result.deleted_count > 0:
            return jsonify({'success': True})
        return jsonify({'success': False, 'message': '未找到支出'})

    except Exception as e:
        print(f"删除支出时出错: {str(e)}")
        return jsonify({'success': False, 'message': str(e)})


@app.route('/fixed-expenses')
@login_required
def fixed_expenses():
    try:
        user_id = ObjectId(session['user_id'])

        # 获取用户的所有固定支出
        expenses = list(mongo.db.fixed_expenses.find({
            'user_id': user_id
        }).sort('date', 1))

        # 计算支付状态和到期日期指示器
        today = datetime.now()
        for expense in expenses:
            # 如果需要，将字符串日期转换为datetime
            if isinstance(expense['date'], str):
                expense['date'] = datetime.strptime(expense['date'], '%Y-%m-%d')

            # 计算距离到期天数
            days_until_due = (expense['date'] - today).days

            # 设置支付状态指示器
            expense['is_paid'] = expense.get('is_paid', False)
            expense['is_overdue'] = days_until_due < 0 and not expense['is_paid']
            expense['is_due_soon'] = 0 <= days_until_due <= 3 and not expense['is_paid']

        # 计算月度总额
        monthly_total = sum(expense['amount'] for expense in expenses if expense['frequency'] == 'monthly')

        # 计算月度平均值（包括季度和年度支出）
        monthly_average = 0
        for expense in expenses:
            if expense['frequency'] == 'monthly':
                monthly_average += expense['amount']
            elif expense['frequency'] == 'quarterly':
                monthly_average += expense['amount'] / 3
            elif expense['frequency'] == 'yearly':
                monthly_average += expense['amount'] / 12

        monthly_average = round(monthly_average, 2)

        # 找出最高支出分类
        category_totals = {}
        for expense in expenses:
            category = expense['category']
            if expense['frequency'] == 'monthly':
                amount = expense['amount']
            elif expense['frequency'] == 'quarterly':
                amount = expense['amount'] / 3
            else:  # 年度
                amount = expense['amount'] / 12
            category_totals[category] = category_totals.get(category, 0) + amount

        highest_category = max(category_totals.items(), key=lambda x: x[1])[0] if category_totals else "无支出"

        # 找出下一个到期日期
        next_due = None
        for expense in expenses:
            if not expense['is_paid'] and expense['date'] > today:
                if next_due is None or expense['date'] < next_due:
                    next_due = expense['date']

        next_due_date = next_due.strftime('%Y年%m月%d日') if next_due else "无即将到期的支出"

        return render_template('fixed_expenses.html',
                               expenses=expenses,
                               monthly_total=monthly_total,
                               monthly_average=monthly_average,
                               highest_category=highest_category,
                               next_due_date=next_due_date)
    except Exception as e:
        print(f"固定支出错误: {str(e)}")
        flash('加载支出时出错', 'error')
        return redirect(url_for('finance_management'))


@app.route('/add-fixed-expense', methods=['POST'])
@login_required
def add_fixed_expense():
    try:
        user_id = ObjectId(session['user_id'])

        # 获取表单数据
        name = request.form.get('expenseName')
        amount = float(request.form.get('expenseAmount'))
        category = request.form.get('expenseCategory')
        frequency = request.form.get('expenseFrequency')
        description = request.form.get('expenseDescription')
        date = datetime.strptime(request.form.get('expenseDate'), '%Y-%m-%d')

        # 创建支出文档
        expense = {
            'user_id': user_id,
            'name': name,
            'amount': amount,
            'category': category,
            'frequency': frequency,
            'description': description,
            'date': date,
            'created_at': datetime.utcnow()
        }

        # 插入数据库
        result = mongo.db.fixed_expenses.insert_one(expense)

        if result.inserted_id:
            flash('固定支出添加成功！', 'success')
        else:
            flash('添加固定支出时出错', 'error')

    except Exception as e:
        print(f"添加固定支出时出错: {str(e)}")
        flash('添加固定支出时出错', 'error')

    return redirect(url_for('fixed_expenses'))


@app.route('/delete-fixed-expense/<expense_id>', methods=['POST'])
@login_required
def delete_fixed_expense(expense_id):
    try:
        user_id = ObjectId(session['user_id'])

        # 删除支出
        result = mongo.db.fixed_expenses.delete_one({
            '_id': ObjectId(expense_id),
            'user_id': user_id
        })

        if result.deleted_count > 0:
            return jsonify({'success': True})
        return jsonify({'success': False, 'message': '未找到支出'})

    except Exception as e:
        print(f"删除固定支出时出错: {str(e)}")
        return jsonify({'success': False, 'message': str(e)})


@app.route('/update-payment-status/<expense_id>', methods=['POST'])
@login_required
def update_payment_status(expense_id):
    try:
        user_id = ObjectId(session['user_id'])
        data = request.get_json()
        is_paid = data.get('is_paid', False)

        # 更新支付状态
        result = mongo.db.fixed_expenses.update_one(
            {
                '_id': ObjectId(expense_id),
                'user_id': user_id
            },
            {
                '$set': {
                    'is_paid': is_paid,
                    'paid_at': datetime.utcnow() if is_paid else None
                }
            }
        )

        if result.modified_count > 0:
            return jsonify({'success': True})
        return jsonify({'success': False, 'message': '未找到支出'})

    except Exception as e:
        print(f"更新支付状态时出错: {str(e)}")
        return jsonify({'success': False, 'message': str(e)})


@app.route('/medicine-management')
@login_required
def medicine_management():
    try:
        user_id = ObjectId(session['user_id'])
        print(f"为用户ID获取药品: {user_id}")

        # 获取用户的所有药品
        medicines = list(mongo.db.medicines.find({
            'user_id': user_id
        }).sort('name', 1))
        print(f"在数据库中找到 {len(medicines)} 种药品")

        # 获取今日药品计划
        today = datetime.now()
        today_start = datetime.combine(today.date(), datetime.min.time())
        today_end = datetime.combine(today.date(), datetime.max.time())
        print(f"查找 {today_start} 到 {today_end} 之间的药品")

        today_medicines = list(mongo.db.medicine_schedule.find({
            'user_id': user_id,
            'date': {
                '$gte': today_start,
                '$lte': today_end
            },
            'is_taken': False
        }).sort('time', 1))
        print(f"找到 {len(today_medicines)} 种今日计划的药品")

        # 获取今日已服用的药品
        taken_medicines = list(mongo.db.medicine_schedule.find({
            'user_id': user_id,
            'date': {
                '$gte': today_start,
                '$lte': today_end
            },
            'is_taken': True
        }).sort('time', 1))
        print(f"找到 {len(taken_medicines)} 种今日已服用的药品")

        # 获取此页面的任何待处理通知
        notification = session.pop('medicine_notification', None)

        return render_template('medicine_management.html',
                               medicines=medicines,
                               today_medicines=today_medicines,
                               taken_medicines=taken_medicines,
                               notification=notification)
    except Exception as e:
        print(f"药品管理错误: {str(e)}")
        session['medicine_notification'] = {'type': 'error', 'message': '加载药品管理页面时出错'}
        return redirect(url_for('dashboard'))


@app.route('/add-medicine', methods=['POST'])
@login_required
def add_medicine():
    try:
        user_id = ObjectId(session['user_id'])
        print(f"为用户ID添加药品: {user_id}")

        # 获取表单数据
        name = request.form.get('medicineName')
        dosage = request.form.get('dosage')
        frequency = request.form.get('frequency')
        times = request.form.getlist('times[]')
        days = request.form.getlist('days')
        notes = request.form.get('notes')

        print(f"收到的药品数据: 名称={name}, 剂量={dosage}, 频率={frequency}")
        print(f"时间: {times}, 天数: {days}")

        if not times:
            session['medicine_notification'] = {'type': 'error', 'message': '请提供至少一个服药时间'}
            return redirect(url_for('medicine_management'))

        if not days:
            session['medicine_notification'] = {'type': 'error', 'message': '请选择至少一天'}
            return redirect(url_for('medicine_management'))

        # 创建药品文档
        medicine = {
            'user_id': user_id,
            'name': name,
            'dosage': dosage,
            'frequency': frequency,
            'times': times,
            'days': days,
            'notes': notes,
            'created_at': datetime.utcnow()
        }

        # 插入数据库
        result = mongo.db.medicines.insert_one(medicine)
        print(f"插入药品ID: {result.inserted_id}")

        if result.inserted_id:
            # 为未来30天创建计划条目
            schedule_entries = []
            start_date = datetime.now().date()

            for i in range(30):
                current_date = start_date + timedelta(days=i)
                day_name = current_date.strftime('%A').lower()

                if day_name in days:
                    for time_str in times:
                        time_obj = datetime.strptime(time_str, '%H:%M').time()
                        schedule_datetime = datetime.combine(current_date, time_obj)

                        schedule_entries.append({
                            'user_id': user_id,
                            'medicine_id': result.inserted_id,
                            'medicine_name': name,
                            'dosage': dosage,
                            'time': time_str,
                            'date': schedule_datetime,
                            'is_taken': False,
                            'created_at': datetime.utcnow()
                        })

            if schedule_entries:
                print(f"创建 {len(schedule_entries)} 个计划条目")
                schedule_result = mongo.db.medicine_schedule.insert_many(schedule_entries)
                print(f"插入 {len(schedule_result.inserted_ids)} 个计划条目")

            session['medicine_notification'] = {'type': 'success', 'message': '药品添加成功！'}
        else:
            session['medicine_notification'] = {'type': 'error', 'message': '添加药品时出错'}

    except Exception as e:
        print(f"添加药品时出错: {str(e)}")
        session['medicine_notification'] = {'type': 'error', 'message': '添加药品时出错'}

    return redirect(url_for('medicine_management'))


@app.route('/update-medicine-status/<schedule_id>', methods=['POST'])
@login_required
def update_medicine_status(schedule_id):
    try:
        user_id = ObjectId(session['user_id'])
        data = request.get_json()
        is_taken = data.get('is_taken', False)

        # 更新药品状态
        result = mongo.db.medicine_schedule.update_one(
            {
                '_id': ObjectId(schedule_id),
                'user_id': user_id
            },
            {
                '$set': {
                    'is_taken': is_taken,
                    'taken_at': datetime.utcnow() if is_taken else None
                }
            }
        )

        if result.modified_count > 0:
            return jsonify({'success': True})
        return jsonify({'success': False, 'message': '未找到药品计划'})

    except Exception as e:
        print(f"更新药品状态时出错: {str(e)}")
        return jsonify({'success': False, 'message': str(e)})


@app.route('/delete-medicine/<medicine_id>', methods=['POST'])
@login_required
def delete_medicine(medicine_id):
    try:
        user_id = ObjectId(session['user_id'])

        # 删除药品及其计划
        mongo.db.medicines.delete_one({
            '_id': ObjectId(medicine_id),
            'user_id': user_id
        })

        mongo.db.medicine_schedule.delete_many({
            'medicine_id': ObjectId(medicine_id),
            'user_id': user_id
        })

        return jsonify({'success': True})

    except Exception as e:
        print(f"删除药品时出错: {str(e)}")
        return jsonify({'success': False, 'message': str(e)})


@app.route('/reminders')
@login_required
def reminders():
    try:
        user_id = ObjectId(session['user_id'])
        now = datetime.now()
        today = now.date()
        tomorrow = today + timedelta(days=1)
        day_after = today + timedelta(days=2)

        # 未来2天的提醒
        next_two_days = list(mongo.db.reminders.find({
            'user_id': user_id,
            'completed': False,
            'date': {'$gte': today.strftime('%Y-%m-%d'), '$lte': day_after.strftime('%Y-%m-%d')}
        }).sort([('date', 1), ('time', 1)]))

        # 即将到来的提醒（未来2天之后）
        upcoming = list(mongo.db.reminders.find({
            'user_id': user_id,
            'completed': False,
            'date': {'$gt': day_after.strftime('%Y-%m-%d')}
        }).sort([('date', 1), ('time', 1)]))

        # 已完成的提醒
        completed = list(mongo.db.reminders.find({
            'user_id': user_id,
            'completed': True
        }).sort([('completed_at', -1)]))

        # 获取此页面的任何待处理通知
        notification = session.pop('reminder_notification', None)

        return render_template('reminders.html',
                               next_two_days=next_two_days,
                               upcoming=upcoming,
                               completed=completed,
                               notification=notification)
    except Exception as e:
        print(f"提醒路由错误: {str(e)}")
        session['reminder_notification'] = {'type': 'error', 'message': '加载提醒时出错'}
        return redirect(url_for('dashboard'))


@app.route('/add-reminder', methods=['POST'])
@login_required
def add_reminder():
    try:
        user_id = ObjectId(session['user_id'])
        title = request.form.get('title')
        description = request.form.get('description')
        date = request.form.get('date')
        time = request.form.get('time')

        if not (title and date and time):
            session['reminder_notification'] = {'type': 'error', 'message': '标题、日期和时间是必填项！'}
            return redirect(url_for('reminders'))

        reminder = {
            'user_id': user_id,
            'title': title,
            'description': description,
            'date': date,
            'time': time,
            'completed': False,
            'created_at': datetime.utcnow()
        }

        result = mongo.db.reminders.insert_one(reminder)
        if result.inserted_id:
            session['reminder_notification'] = {'type': 'success', 'message': '提醒添加成功！'}
        else:
            session['reminder_notification'] = {'type': 'error', 'message': '添加提醒时出错'}

    except Exception as e:
        print(f"添加提醒时出错: {str(e)}")
        session['reminder_notification'] = {'type': 'error', 'message': '添加提醒时出错'}

    return redirect(url_for('reminders'))


@app.route('/complete-reminder/<reminder_id>', methods=['POST'])
@login_required
def complete_reminder(reminder_id):
    user_id = ObjectId(session['user_id'])
    result = mongo.db.reminders.update_one(
        {'_id': ObjectId(reminder_id), 'user_id': user_id},
        {'$set': {'completed': True, 'completed_at': datetime.utcnow()}}
    )
    if result.modified_count > 0:
        return jsonify({'success': True})
    return jsonify({'success': False, 'message': '未找到提醒或提醒已完成'})


@app.route('/delete-reminder/<reminder_id>', methods=['POST'])
@login_required
def delete_reminder(reminder_id):
    try:
        user_id = ObjectId(session['user_id'])
        result = mongo.db.reminders.delete_one({
            '_id': ObjectId(reminder_id),
            'user_id': user_id
        })

        if result.deleted_count > 0:
            return jsonify({'success': True})
        return jsonify({'success': False, 'message': '未找到提醒'})
    except Exception as e:
        print(f"删除提醒时出错: {str(e)}")
        return jsonify({'success': False, 'message': str(e)})


@app.route('/create-emergency-log', methods=['POST'])
@login_required
def create_emergency_log():
    try:
        data = request.get_json()
        if not data or 'contact_type' not in data or 'phone_number' not in data:
            return jsonify({'success': False, 'message': '缺少必填字段'}), 400

        user = mongo.db.users.find_one({'_id': ObjectId(session['user_id'])})
        if not user:
            return jsonify({'success': False, 'message': '未找到用户'}), 404

        # 创建紧急日志
        log = {
            'user_id': ObjectId(session['user_id']),
            'user_name': user['name'],
            'contact_type': data['contact_type'],
            'phone_number': data['phone_number'],
            'created_at': datetime.utcnow()
        }

        # 如果用户是老年人，添加关联子女信息
        if user['role'] == 'elder':
            linked_child = mongo.db.users.find_one({
                'elder_id': str(user['_id']),
                'role': 'child'
            })
            if linked_child:
                log['linked_child_id'] = linked_child['_id']
                log['linked_child_name'] = linked_child['name']

        result = mongo.db.emergency_logs.insert_one(log)
        if result.inserted_id:
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'message': '创建紧急日志失败'}), 500

    except Exception as e:
        print(f"创建紧急日志时出错: {str(e)}")
        return jsonify({'success': False, 'message': '创建紧急日志时出错'}), 500

# ---- AI Assistant Page ----
@app.route('/assistant/chat')
def assistant_chat():
    return render_template('ai_assistant.html')

# ---- AI Assistant API ----
@app.route('/assistant/api', methods=['POST'])
@login_required
def assistant_api():
    try:
        data = request.get_json(silent=True) or {}
        user_message = data.get("message", "").strip()

        if not user_message:
            return jsonify({"error": "没有收到有效信息"}), 400

        # 读取环境变量
        QWEN_API_KEY = os.getenv("QWEN_API_KEY")
        QWEN_API_BASE = os.getenv("QWEN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen3-next-80b-a3b-instruct")

        if not QWEN_API_KEY:
            return jsonify({"error": "服务器未配置 QWEN_API_KEY"}), 500

        # 生成 system prompt —— 增强安全性 + 健康平台定位
        system_prompt = (
            "你是 JNU 智慧康养平台的 AI 健康助手，主要为中老年人提供帮助。\n"
            "你的任务：\n"
            "1. 提供温和、易懂、通俗易理解的解释。\n"
            "2. 可以提供健康建议，但禁止医学诊断或药物剂量建议。\n"
            "3. 如需具体诊疗，一律建议咨询专业医生。\n"
            "4. 回答尽量短、重点明确、适合老年用户阅读。\n"
        )

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {QWEN_API_KEY}",
        }

        payload = {
            "model": QWEN_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": 0.3,
            "max_tokens": 1024,
        }

        url = QWEN_API_BASE.rstrip("/") + "/chat/completions"

        # 发起请求
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()

        result = response.json()
        reply = result.get("choices", [{}])[0].get("message", {}).get("content", "")

        if not reply:
            return jsonify({"error": "AI 无有效回复"}), 500

        return jsonify({"reply": reply})

    except requests.exceptions.Timeout:
        return jsonify({"error": "AI 服务响应超时，请稍后再试"}), 504

    except requests.exceptions.RequestException as e:
        return jsonify({"error": "AI 服务连接失败", "detail": str(e)}), 500

    except Exception as e:
        print(f"AI 助手错误: {str(e)}")
        return jsonify({"error": "AI 服务内部错误", "detail": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))  # 使用服务器分配的端口
    app.run(host="0.0.0.0", port=port, debug=False)  # debug=False 生产环境安全
