/* ==========================================================================
   2RTK NTRIP Caster — 前端国际化（zh-CN / en）
   --------------------------------------------------------------------------
   设计说明：
   1) 纯前端方案，不依赖后端 locale。语言选择写 localStorage，?lang=zh|en 可强制覆盖。
   2) 静态标记用 data-i18n* 属性，JS 动态内容一律走 t('key')，避免两套文案来源。
   3) 后端 API 的提示语目前是中文硬编码（src/web.py），这里用 localizeServerMessage()
      做一层收敛：en 模式下把已知中文串翻成英文，zh 模式把少量英文串翻回中文。
      这样不用改后端协议就能保证两种语言下弹窗文案一致。
   4) 新增文案只改本文件：zh / en 两份字典必须保持同样的键，缺键时回落到键名，
      开发期一眼能看出来（并在控制台打一次警告）。
   ========================================================================== */
(function (global) {
    'use strict';

    var STORAGE_KEY = 'ntripcaster.lang';
    var SUPPORTED = ['zh', 'en'];
    var FALLBACK = 'zh';

    /* ---------------- 字典 ---------------- */
    var DICT = {
        en: {
            /* 通用 */
            'app.console': 'Caster Console',
            'common.loading': 'Loading…',
            'common.unknown': 'Unknown',
            'common.ok': 'OK',
            'common.cancel': 'Cancel',
            'common.confirm': 'Confirm',
            'common.save': 'Save',
            'common.add': 'Add',
            'common.edit': 'Edit',
            'common.delete': 'Delete',
            'common.close': 'Close',
            'common.unspecified': 'Unspecified',
            'common.total': 'Total',
            'common.page': 'Page',
            'common.prev': 'Previous',
            'common.next': 'Next',
            'common.perPage': 'Per page',
            'common.search': 'Search',
            'common.searchPlaceholder': 'Type to filter…',
            'common.searchClear': 'Clear',

            /* 顶栏 / 导航 */
            'nav.dashboard': 'Overview',
            'nav.mounts': 'Mount Points',
            'nav.users': 'Users',
            'nav.monitor': 'Live Data',
            'nav.settings': 'Settings',
            'nav.donate': 'Donate',
            'nav.logout': 'Sign out',
            'nav.main': 'Main navigation',
            'lang.label': 'Language',

            /* 实时通道状态 */
            'rt.connecting': 'Connecting…',
            'rt.connected': 'Live',
            'rt.disconnected': 'Disconnected',
            'rt.reconnecting': 'Reconnecting…',
            'rt.failed': 'Offline',
            'rt.unknown': 'Unknown',
            'rt.channel': 'Realtime channel',

            /* 日志面板 */
            'log.title': 'System Logs',
            'log.ready': 'Console connected, waiting for events…',

            /* 登录页 */
            'login.pageTitle': 'Sign in',
            'login.subtitle': 'Administrator sign in',
            'login.username': 'Username',
            'login.password': 'Password',
            'login.submit': 'Sign in',
            'login.submitting': 'Signing in…',
            'login.hint': 'Default credentials',
            'login.hintTail': '— change it in Settings before going live.',
            'login.redirect.users': 'User Management',
            'login.redirect.mounts': 'Mount Point Management',
            'login.redirect.settings': 'System Settings',
            'login.redirect.other': 'that section',
            'login.redirectPrefix': 'Sign in as administrator to open',
            'login.validation.userMin': 'Username must be at least 2 characters',
            'login.validation.userMax': 'Username cannot exceed 50 characters',
            'login.validation.userChars': 'Username contains invalid characters',
            'login.validation.pwdMin': 'Password must be at least 6 characters',
            'login.validation.pwdMax': 'Password cannot exceed 100 characters',

            /* 总览 */
            'dash.title': 'System Status',
            'dash.updated': 'Updated {time}',
            'dash.uptime': 'Uptime',
            'dash.onlineMounts': 'Online Mounts',
            'dash.onlineUsers': 'Online Users',
            'dash.connections': '{n} connections',
            'dash.connectionOne': '{n} connection',
            'dash.dataTransfer': 'Data Transfer',
            'dash.runtime': 'Runtime',
            'dash.cpu': 'CPU Usage',
            'dash.memory': 'Memory',
            'dash.network': 'Network',
            'dash.activeConnections': 'Active connections',
            'dash.maxConcurrent': 'Max concurrent',
            'dash.totalConnections': 'Total connections',
            'dash.rejected': 'Rejected',
            'dash.onlineMountPoints': 'Online mount points',
            'dash.userConnections': 'User connections',
            'dash.mountDetails': 'Mount Point Details',
            'dash.noMounts': 'No mount point is online right now',
            'dash.loadFailed': 'Failed to load page content, please try again later.',

            /* 用户页 */
            'users.title': 'User Management',
            'users.add': 'Add User',
            'users.col.username': 'Username',
            'users.col.status': 'Status',
            'users.col.connections': 'Connections',
            'users.col.connectTime': 'Connect Time',
            'users.col.actions': 'Actions',
            'users.online': 'Online',
            'users.offline': 'Offline',
            'users.addTitle': 'Add User',
            'users.editTitle': 'Edit User',
            'users.adminLocked': 'Administrator username cannot be modified',
            'users.newPasswordOptional': 'New Password (Optional)',
            'users.confirmDeleteTitle': 'Confirm Delete User',
            'users.confirmDelete': 'Are you sure you want to delete user "{name}"? This action cannot be undone.',

            /* 挂载点页 */
            'mounts.title': 'Mount Point Management',
            'mounts.add': 'Add Mount Point',
            'mounts.col.mount': 'Mount Point',
            'mounts.col.status': 'Status',
            'mounts.col.connections': 'Connections',
            'mounts.col.owner': 'Owner',
            'mounts.col.description': 'Description',
            'mounts.col.actions': 'Actions',
            'mounts.addTitle': 'Add Mount Point',
            'mounts.editTitle': 'Edit Mount Point',
            'mounts.name': 'Mount Point Name',
            'mounts.password': 'Password (NTRIP 1.0)',
            'mounts.newPassword': 'New Password (NTRIP 1.0)',
            'mounts.bindUser': 'Bind User (NTRIP 2.0)',
            'mounts.noBinding': 'No user binding',
            'mounts.confirmDeleteTitle': 'Confirm Delete Mount Point',
            'mounts.confirmDelete': 'Are you sure you want to delete mount point "{name}"? This action cannot be undone.',

            /* 表单占位符 */
            'ph.username': 'Enter username',
            'ph.password': 'Enter password',
            'ph.keepPassword': 'Leave blank to keep current password',
            'ph.mountName': 'Enter mount point name',
            'ph.bindUser': 'Enter username, leave blank for no binding',
            'ph.newPassword': 'At least 6 characters',
            'ph.repeatPassword': 'Repeat the new password',

            /* 监控页 */
            'monitor.title': 'Base Station STR Information',
            'monitor.subtitle': 'Real-time monitoring of NTRIP data streams and base station status',
            'monitor.strTable': 'STR Data Table',
            'monitor.strLoading': 'Loading STR table data...',
            'monitor.strRefreshing': 'Refreshing STR table data...',
            'monitor.strEmpty': 'No STR table data available',
            'monitor.stationInfo': 'Base Station Information',
            'monitor.waitSelect': 'Waiting for selection',
            'monitor.selected': 'Selected',
            'monitor.selectHint': 'Please click the INFO button in the STR table to select a mount point',
            'monitor.stationLocation': 'Base Station Location',
            'monitor.waitLocation': 'Waiting for location data...',
            'monitor.satViz': 'Satellite Data Visualization',
            'monitor.waitData': 'Waiting for data',
            'monitor.receiving': 'Receiving',
            'monitor.waitSat': 'Waiting for satellite data...',
            'monitor.mapAmap': 'AutoNavi',
            'monitor.mapOsm': 'OpenStreetMap',
            'monitor.info': 'INFO',
            'monitor.noData': 'No data available',
            'monitor.rtcmStartFailed': 'Failed to start RTCM parsing: {error}',
            'monitor.rtcmApiFailed': 'Failed to call RTCM parsing API',
            'monitor.rtcmParsing': 'Parsing RTCM data for {name}…',
            'monitor.infoTitle': 'View real-time RTCM parsing for {name}',

            /* 基准站字段 */
            'station.mount': 'Mount Point:',
            'station.id': 'Station ID:',
            'station.country': 'Country:',
            'station.city': 'City:',
            'station.receiverType': 'Receiver Type:',
            'station.receiverFw': 'Receiver Firmware:',
            'station.antennaType': 'Antenna Type:',
            'station.antennaSerial': 'Antenna Serial:',
            'station.coordinates': 'Coordinates:',
            'station.longitude': 'Longitude:',
            'station.latitude': 'Latitude:',
            'station.height': 'Height:',
            'station.ecef': 'ECEF:',

            /* 卫星提示框 */
            'sat.signal': 'Signal Strength:',
            'sat.elevation': 'Elevation:',
            'sat.azimuth': 'Azimuth:',
            'sat.band': 'Band:',
            'sat.frequency': 'Frequency:',
            'sat.channel': 'Channel:',

            /* 设置页 */
            'settings.title': 'System Settings',
            'settings.security': 'Security',
            'settings.newPassword': 'New password',
            'settings.confirmPassword': 'Confirm password',
            'settings.passwordHint': 'Changing the administrator password does not sign you out.',
            'settings.changePassword': 'Change admin password',
            'settings.systemControl': 'System control',
            'settings.restartHint': 'Restarting reloads the configuration file and drops all NTRIP sessions for a few seconds.',
            'settings.restart': 'Restart program',
            'settings.confirmRestartTitle': 'Confirm Restart',
            'settings.confirmRestart': 'Are you sure you want to restart the program? All connections will be disconnected after restart, please proceed with caution!',
            'settings.restartSent': 'Program restart command sent, system will restart in 3 seconds...',
            'settings.restartFailed': 'Restart failed: {error}',

            /* 校验与提示 */
            'valid.required': '{field} cannot be empty',
            'valid.charset': '{field} can only contain English letters, numbers, underscores and hyphens, no other special symbols, Chinese characters or other characters are allowed',
            'field.username': 'Username',
            'field.password': 'Password',
            'field.newPassword': 'New password',
            'field.confirmPassword': 'Confirm password',
            'field.mountName': 'Mount point name',
            'valid.userNameLen': 'Username length must be between 3-50 characters',
            'valid.passwordLen': 'Password length must be between 6-100 characters',
            'valid.mountNameLen': 'Mount point name length must be between 3-50 characters',
            'valid.noChanges': 'No changes made',
            'valid.passwordTooShort': 'Password must be at least 6 characters long',
            'valid.passwordMismatch': 'The two passwords entered do not match',
            'valid.passwordRequired': 'Please enter new password and confirm password',

            'err.loginExpired': 'Login expired, please log in again',
            'err.unauthorized': 'Unauthorized access',
            'err.unknown': 'Unknown error',

            'msg.userAddFailed': 'Failed to add user: {error}',
            'msg.userUpdateFailed': 'Failed to update user: {error}',
            'msg.userDeleteFailed': 'Failed to delete user: {error}',
            'msg.mountAddFailed': 'Failed to add mount point: {error}',
            'msg.mountUpdateFailed': 'Failed to update mount point: {error}',
            'msg.mountDeleteFailed': 'Failed to delete mount point: {error}',
            'msg.passwordChanged': 'Administrator password changed successfully',
            'msg.passwordChangeFailed': 'Failed to change password: {error}',
            'msg.restartInvokeFailed': 'Failed to restart program: {error}',
            'msg.httpError': 'Error: {error}',

            'confirm.logoutTitle': 'Confirm Logout',
            'confirm.logout': 'Are you sure you want to logout?',

            /* 打赏 */
            'donate.title': 'Support this project',
            'donate.subtitle': 'If this project saves you time, you are welcome to buy the author a coffee. Every bit helps keep it maintained.',
            'donate.alipay': 'Alipay',
            'donate.wechat': 'WeChat Pay',
            'donate.scanAlipay': 'Open Alipay and scan the code',
            'donate.scanWechat': 'Open WeChat and scan the code',
            'donate.thanks': 'Thank you for your support!',
            'donate.note': 'Donations are voluntary and non-refundable. They do not unlock any additional features.',
            'donate.open': 'Donate',
            'donate.close': 'Maybe later'
        },

        zh: {
            /* 通用 */
            'app.console': '播发服务控制台',
            'common.loading': '加载中…',
            'common.unknown': '未知',
            'common.ok': '确定',
            'common.cancel': '取消',
            'common.confirm': '确认',
            'common.save': '保存',
            'common.add': '添加',
            'common.edit': '编辑',
            'common.delete': '删除',
            'common.close': '关闭',
            'common.unspecified': '未指定',
            'common.total': '共',
            'common.page': '页',
            'common.prev': '上一页',
            'common.next': '下一页',
            'common.perPage': '每页',
            'common.search': '搜索',
            'common.searchPlaceholder': '输入关键字筛选…',
            'common.searchClear': '清除',

            /* 顶栏 / 导航 */
            'nav.dashboard': '总览',
            'nav.mounts': '挂载点',
            'nav.users': '用户',
            'nav.monitor': '实时数据',
            'nav.settings': '设置',
            'nav.donate': '打赏',
            'nav.logout': '退出登录',
            'nav.main': '主导航',
            'lang.label': '语言',

            /* 实时通道状态 */
            'rt.connecting': '连接中…',
            'rt.connected': '实时',
            'rt.disconnected': '已断开',
            'rt.reconnecting': '重连中…',
            'rt.failed': '离线',
            'rt.unknown': '未知',
            'rt.channel': '实时推送通道',

            /* 日志面板 */
            'log.title': '系统日志',
            'log.ready': '控制台已连接，等待事件…',

            /* 登录页 */
            'login.pageTitle': '登录',
            'login.subtitle': '管理员登录',
            'login.username': '用户名',
            'login.password': '密码',
            'login.submit': '登录',
            'login.submitting': '登录中…',
            'login.hint': '默认账号',
            'login.hintTail': '— 上线前请先在“设置”里修改。',
            'login.redirect.users': '用户管理',
            'login.redirect.mounts': '挂载点管理',
            'login.redirect.settings': '系统设置',
            'login.redirect.other': '该页面',
            'login.redirectPrefix': '请先用管理员账号登录后再打开',
            'login.validation.userMin': '用户名至少 2 个字符',
            'login.validation.userMax': '用户名不能超过 50 个字符',
            'login.validation.userChars': '用户名包含非法字符',
            'login.validation.pwdMin': '密码至少 6 个字符',
            'login.validation.pwdMax': '密码不能超过 100 个字符',

            /* 总览 */
            'dash.title': '系统状态',
            'dash.updated': '更新于 {time}',
            'dash.uptime': '运行时长',
            'dash.onlineMounts': '在线挂载点',
            'dash.onlineUsers': '在线用户',
            'dash.connections': '{n} 个连接',
            'dash.connectionOne': '{n} 个连接',
            'dash.dataTransfer': '数据传输',
            'dash.runtime': '运行状态',
            'dash.cpu': 'CPU 使用率',
            'dash.memory': '内存',
            'dash.network': '网络',
            'dash.activeConnections': '活动连接',
            'dash.maxConcurrent': '最大并发',
            'dash.totalConnections': '累计连接',
            'dash.rejected': '已拒绝',
            'dash.onlineMountPoints': '在线挂载点',
            'dash.userConnections': '用户连接数',
            'dash.mountDetails': '挂载点详情',
            'dash.noMounts': '当前没有在线挂载点',
            'dash.loadFailed': '页面内容加载失败，请稍后重试。',

            /* 用户页 */
            'users.title': '用户管理',
            'users.add': '添加用户',
            'users.col.username': '用户名',
            'users.col.status': '状态',
            'users.col.connections': '连接数',
            'users.col.connectTime': '连接时间',
            'users.col.actions': '操作',
            'users.online': '在线',
            'users.offline': '离线',
            'users.addTitle': '添加用户',
            'users.editTitle': '编辑用户',
            'users.adminLocked': '管理员用户名不可修改',
            'users.newPasswordOptional': '新密码（可留空）',
            'users.confirmDeleteTitle': '确认删除用户',
            'users.confirmDelete': '确定要删除用户“{name}”吗？此操作不可撤销。',

            /* 挂载点页 */
            'mounts.title': '挂载点管理',
            'mounts.add': '添加挂载点',
            'mounts.col.mount': '挂载点',
            'mounts.col.status': '状态',
            'mounts.col.connections': '连接数',
            'mounts.col.owner': '所属用户',
            'mounts.col.description': '描述',
            'mounts.col.actions': '操作',
            'mounts.addTitle': '添加挂载点',
            'mounts.editTitle': '编辑挂载点',
            'mounts.name': '挂载点名称',
            'mounts.password': '密码（NTRIP 1.0）',
            'mounts.newPassword': '新密码（NTRIP 1.0）',
            'mounts.bindUser': '绑定用户（NTRIP 2.0）',
            'mounts.noBinding': '不绑定用户',
            'mounts.confirmDeleteTitle': '确认删除挂载点',
            'mounts.confirmDelete': '确定要删除挂载点“{name}”吗？此操作不可撤销。',

            /* 表单占位符 */
            'ph.username': '请输入用户名',
            'ph.password': '请输入密码',
            'ph.keepPassword': '留空表示不修改密码',
            'ph.mountName': '请输入挂载点名称',
            'ph.bindUser': '输入用户名，留空表示不绑定',
            'ph.newPassword': '至少 6 个字符',
            'ph.repeatPassword': '再次输入新密码',

            /* 监控页 */
            'monitor.title': '基准站 STR 信息',
            'monitor.subtitle': '实时监控 NTRIP 数据流与基准站状态',
            'monitor.strTable': 'STR 数据表',
            'monitor.strLoading': '正在加载 STR 表数据…',
            'monitor.strRefreshing': '正在刷新 STR 表数据…',
            'monitor.strEmpty': '暂无 STR 表数据',
            'monitor.stationInfo': '基准站信息',
            'monitor.waitSelect': '等待选择',
            'monitor.selected': '已选择',
            'monitor.selectHint': '请点击 STR 表中的 INFO 按钮选择一个挂载点',
            'monitor.stationLocation': '基准站位置',
            'monitor.waitLocation': '等待定位数据…',
            'monitor.satViz': '卫星数据可视化',
            'monitor.waitData': '等待数据',
            'monitor.receiving': '接收中',
            'monitor.waitSat': '等待卫星数据…',
            'monitor.mapAmap': '高德地图',
            'monitor.mapOsm': 'OpenStreetMap',
            'monitor.info': 'INFO',
            'monitor.noData': '暂无数据',
            'monitor.rtcmStartFailed': '启动 RTCM 解析失败：{error}',
            'monitor.rtcmApiFailed': '调用 RTCM 解析接口失败',
            'monitor.rtcmParsing': '正在解析 {name} 的 RTCM 数据…',
            'monitor.infoTitle': '查看 {name} 的实时 RTCM 解析',

            /* 基准站字段 */
            'station.mount': '挂载点：',
            'station.id': '站点编号：',
            'station.country': '国家：',
            'station.city': '城市：',
            'station.receiverType': '接收机型号：',
            'station.receiverFw': '接收机固件：',
            'station.antennaType': '天线型号：',
            'station.antennaSerial': '天线序列号：',
            'station.coordinates': '坐标：',
            'station.longitude': '经度：',
            'station.latitude': '纬度：',
            'station.height': '高程：',
            'station.ecef': 'ECEF：',

            /* 卫星提示框 */
            'sat.signal': '信号强度：',
            'sat.elevation': '高度角：',
            'sat.azimuth': '方位角：',
            'sat.band': '频段：',
            'sat.frequency': '频率：',
            'sat.channel': '信号通道：',

            /* 设置页 */
            'settings.title': '系统设置',
            'settings.security': '安全',
            'settings.newPassword': '新密码',
            'settings.confirmPassword': '确认密码',
            'settings.passwordHint': '修改管理员密码不会让当前登录失效。',
            'settings.changePassword': '修改管理员密码',
            'settings.systemControl': '系统控制',
            'settings.restartHint': '重启会重新加载配置文件，并中断所有 NTRIP 会话数秒。',
            'settings.restart': '重启程序',
            'settings.confirmRestartTitle': '确认重启',
            'settings.confirmRestart': '确定要重启程序吗？重启后所有连接都会断开，请谨慎操作！',
            'settings.restartSent': '重启指令已发送，系统将在 3 秒后重启…',
            'settings.restartFailed': '重启失败：{error}',

            /* 校验与提示 */
            'valid.required': '{field}不能为空',
            'valid.charset': '{field}只能包含英文字母、数字、下划线和短横线，不允许其他特殊符号和中文',
            'field.username': '用户名',
            'field.password': '密码',
            'field.newPassword': '新密码',
            'field.confirmPassword': '确认密码',
            'field.mountName': '挂载点名称',
            'valid.userNameLen': '用户名长度必须在 3-50 个字符之间',
            'valid.passwordLen': '密码长度必须在 6-100 个字符之间',
            'valid.mountNameLen': '挂载点名称长度必须在 3-50 个字符之间',
            'valid.noChanges': '没有做任何修改',
            'valid.passwordTooShort': '密码长度至少 6 个字符',
            'valid.passwordMismatch': '两次输入的密码不一致',
            'valid.passwordRequired': '请输入新密码和确认密码',

            'err.loginExpired': '登录已过期，请重新登录',
            'err.unauthorized': '未授权访问',
            'err.unknown': '未知错误',

            'msg.userAddFailed': '添加用户失败：{error}',
            'msg.userUpdateFailed': '更新用户失败：{error}',
            'msg.userDeleteFailed': '删除用户失败：{error}',
            'msg.mountAddFailed': '添加挂载点失败：{error}',
            'msg.mountUpdateFailed': '更新挂载点失败：{error}',
            'msg.mountDeleteFailed': '删除挂载点失败：{error}',
            'msg.passwordChanged': '管理员密码修改成功',
            'msg.passwordChangeFailed': '修改密码失败：{error}',
            'msg.restartInvokeFailed': '重启程序失败：{error}',
            'msg.httpError': '错误：{error}',

            'confirm.logoutTitle': '确认退出',
            'confirm.logout': '确定要退出登录吗？',

            /* 打赏 */
            'donate.title': '支持本项目',
            'donate.subtitle': '如果这个项目帮你省下了时间，欢迎请作者喝杯咖啡。你的支持是持续维护的动力。',
            'donate.alipay': '支付宝',
            'donate.wechat': '微信支付',
            'donate.scanAlipay': '打开支付宝扫一扫',
            'donate.scanWechat': '打开微信扫一扫',
            'donate.thanks': '感恩支持！感谢有你！',
            'donate.note': '打赏完全自愿，不支持退款，也不会解锁任何额外功能。',
            'donate.open': '打赏作者',
            'donate.close': '以后再说'
        }
    };

    /* ---------------- 后端提示语收敛 ---------------- */
    // 每条：zh 为后端实际下发的正则，en 为英文模板（$1 引用捕获组）
    var SERVER_MESSAGES = [
        { zh: /^未登录或登录已过期$/, en: 'Not signed in or the session has expired' },
        { zh: /^请求数据格式错误$/, en: 'Malformed request payload' },
        { zh: /^用户名和密码不能为空$/, en: 'Username and password are required' },
        { zh: /^用户名长度必须在2-50个字符之间$/, en: 'Username must be 2-50 characters' },
        { zh: /^密码长度必须在6-100个字符之间$/, en: 'Password must be 6-100 characters' },
        { zh: /^用户名包含非法字符$/, en: 'Username contains invalid characters' },
        { zh: /^用户名不能为空$/, en: 'Username cannot be empty' },
        { zh: /^密码不能为空$/, en: 'Password cannot be empty' },
        { zh: /^挂载点名称不能为空$/, en: 'Mount point name cannot be empty' },
        { zh: /^用户名只能包含英文字母、数字、下划线和中横线$/, en: 'Username can only contain letters, digits, underscores and hyphens' },
        { zh: /^密码只能包含英文字母、数字、下划线和中横线$/, en: 'Password can only contain letters, digits, underscores and hyphens' },
        { zh: /^新密码只能包含英文字母、数字、下划线和中横线$/, en: 'New password can only contain letters, digits, underscores and hyphens' },
        { zh: /^挂载点名称只能包含英文字母、数字、下划线和中横线$/, en: 'Mount point name can only contain letters, digits, underscores and hyphens' },
        { zh: /^登录失败次数过多，请 (\d+) 秒后再试$/, en: 'Too many failed attempts, please retry in $1 seconds' },
        { zh: /^用户名或密码错误$/, en: 'Wrong username or password' },
        { zh: /^登录失败$/, en: 'Sign-in failed' },
        { zh: /^挂载点数据不存在或未解析$/, en: 'Mount point data not found or not parsed' },
        { zh: /^程序重启指令已发送$/, en: 'Restart command sent' },
        { zh: /^调试接口，仅在 debug_mode=true 时可用$/, en: 'Debug endpoint, available only when debug_mode=true' },
        { zh: /^挂载点名称过长$/, en: 'Mount point name is too long' },
        { zh: /^新密码不能为空$/, en: 'New password is required' },
        { zh: /^新密码长度必须在6-100个字符之间$/, en: 'New password must be 6-100 characters' },
        { zh: /^管理员密码更新失败$/, en: 'Failed to update the administrator password' },
        { zh: /^管理员口令更新成功$/, en: 'Administrator password updated' },
        { zh: /^管理员 (.+) 密码更新成功$/, en: 'Password updated for administrator $1' },
        { zh: /^用户名已存在$/, en: 'Username already exists' },
        { zh: /^该用户名已被管理员账号占用$/, en: 'That username is reserved for the administrator account' },
        { zh: /^管理员用户名不能修改$/, en: 'Administrator username cannot be changed' },
        { zh: /^请提供要更新的密码或用户名$/, en: 'Provide a password or username to update' },
        { zh: /^用户不存在$/, en: 'User not found' },
        { zh: /^用户 (.+) 删除成功$/, en: 'User $1 deleted' },
        { zh: /^挂载点名称和密码不能为空$/, en: 'Mount point name and password are required' },
        { zh: /^挂载点名称长度必须在2-50个字符之间$/, en: 'Mount point name must be 2-50 characters' },
        { zh: /^指定的用户不存在$/, en: 'The selected user does not exist' },
        { zh: /^参数不能为空$/, en: 'Missing required parameter' },
        { zh: /^未找到该用户$/, en: 'User not found' },
        { zh: /^未找到该挂载点$/, en: 'Mount point not found' },
        { zh: /^删除失败$/, en: 'Delete failed' },
        { zh: /^更新失败$/, en: 'Update failed' },
        { zh: /^创建失败$/, en: 'Create failed' }
    ];

    // 少数后端英文串，在中文模式下翻回来
    var SERVER_MESSAGES_EN = [
        { en: /^Mount not found$/, zh: '挂载点不存在' },
        { en: /^Mount name is required$/, zh: '挂载点名称不能为空' },
        { en: /^Failed to start parsing - mount may be offline$/, zh: '启动解析失败——挂载点可能已离线' },
        { en: /^Mount (.+) initialized$/, zh: '挂载点 $1 已初始化' },
        { en: /^Real-time RTCM parsing started for (.+)$/, zh: '已开始解析 $1 的实时 RTCM 数据' },
        { en: /^Real-time RTCM parsing stopped$/, zh: '已停止实时 RTCM 解析' },
        { en: /^All bypass parsing stopped$/, zh: '已停止全部旁路解析' },
        { en: /^Parser status retrieved successfully$/, zh: '解析器状态获取成功' },
        { en: /^Heartbeat updated$/, zh: '心跳已更新' },
        { en: /^Login successful$/, zh: '登录成功' }
    ];

    /* ---------------- 内部状态 ---------------- */
    var current = FALLBACK;

    function normalize(lang) {
        if (!lang) return null;
        var v = String(lang).toLowerCase();
        if (v.indexOf('zh') === 0) return 'zh';
        if (v.indexOf('en') === 0) return 'en';
        return SUPPORTED.indexOf(v) >= 0 ? v : null;
    }

    function detect() {
        // 1) URL 强制覆盖：?lang=en
        try {
            var q = new URLSearchParams(global.location.search).get('lang');
            var qn = normalize(q);
            if (qn) return qn;
        } catch (e) { /* 老浏览器忽略 */ }

        // 2) 用户上次选择
        try {
            var saved = normalize(global.localStorage.getItem(STORAGE_KEY));
            if (saved) return saved;
        } catch (e) { /* 隐私模式下 localStorage 可能抛异常 */ }

        // 3) 浏览器语言
        var nav = normalize(global.navigator.language || global.navigator.userLanguage);
        return nav || FALLBACK;
    }

    function interpolate(text, vars) {
        if (!vars) return text;
        return String(text).replace(/\{(\w+)\}/g, function (m, key) {
            return Object.prototype.hasOwnProperty.call(vars, key) ? String(vars[key]) : m;
        });
    }

    var warned = {};

    /** 取文案。缺键时返回键名并只在控制台警告一次。 */
    function t(key, vars) {
        var pack = DICT[current] || DICT[FALLBACK];
        var text = pack[key];
        if (text === undefined) {
            text = DICT[FALLBACK][key];
            if (text === undefined) {
                if (!warned[key]) {
                    warned[key] = 1;
                    if (global.console) console.warn('[i18n] missing key:', key);
                }
                return key;
            }
        }
        return interpolate(text, vars);
    }

    /** 把后端返回的提示语按当前语言对齐（已知串才翻，未知原样返回） */
    function localizeServerMessage(message) {
        if (message === null || message === undefined) return '';
        var msg = String(message).trim();
        if (!msg) return msg;

        if (current === 'en') {
            for (var i = 0; i < SERVER_MESSAGES.length; i++) {
                var rule = SERVER_MESSAGES[i];
                if (rule.zh.test(msg)) return msg.replace(rule.zh, rule.en);
            }
        } else {
            for (var j = 0; j < SERVER_MESSAGES_EN.length; j++) {
                var rev = SERVER_MESSAGES_EN[j];
                if (rev.en.test(msg)) return msg.replace(rev.en, rev.zh);
            }
        }
        return msg;
    }

    /** 应用静态 DOM 上的 data-i18n* 标记 */
    function applyI18n(root) {
        var scope = root || global.document;
        if (!scope || !scope.querySelectorAll) return;

        scope.querySelectorAll('[data-i18n]').forEach(function (el) {
            el.textContent = t(el.getAttribute('data-i18n'));
        });
        scope.querySelectorAll('[data-i18n-html]').forEach(function (el) {
            el.innerHTML = t(el.getAttribute('data-i18n-html'));
        });
        scope.querySelectorAll('[data-i18n-ph]').forEach(function (el) {
            el.setAttribute('placeholder', t(el.getAttribute('data-i18n-ph')));
        });
        scope.querySelectorAll('[data-i18n-title]').forEach(function (el) {
            el.setAttribute('title', t(el.getAttribute('data-i18n-title')));
        });
        scope.querySelectorAll('[data-i18n-aria]').forEach(function (el) {
            el.setAttribute('aria-label', t(el.getAttribute('data-i18n-aria')));
        });

        // 语言按钮的选中态
        scope.querySelectorAll('[data-lang]').forEach(function (el) {
            el.classList.toggle('is-active', el.getAttribute('data-lang') === current);
            el.setAttribute('aria-pressed', el.getAttribute('data-lang') === current ? 'true' : 'false');
        });

        global.document.documentElement.setAttribute('lang', current === 'zh' ? 'zh-CN' : 'en');
    }

    function getLang() {
        return current;
    }

    /** 切换语言：写 localStorage → 重刷静态文案 → 广播事件交给 app.js 重绘动态区域 */
    function setLang(lang, options) {
        var next = normalize(lang);
        if (!next || next === current) return current;

        current = next;
        try {
            global.localStorage.setItem(STORAGE_KEY, current);
        } catch (e) { /* 隐私模式忽略 */ }

        // 手动点选后清掉 URL 上的 ?lang=，否则刷新时 URL 参数又会盖掉用户的选择
        try {
            var url = new URL(global.location.href);
            if (url.searchParams.has('lang')) {
                url.searchParams.delete('lang');
                var qs = url.searchParams.toString();
                global.history.replaceState({}, '', url.pathname + (qs ? '?' + qs : '') + url.hash);
            }
        } catch (e) { /* 老浏览器忽略 */ }

        applyI18n(global.document);

        if (!(options && options.silent)) {
            try {
                global.dispatchEvent(new CustomEvent('i18n:changed', { detail: { lang: current } }));
            } catch (e) {
                // 极老浏览器没有 CustomEvent 构造器：退化成普通事件
                var evt = global.document.createEvent('Event');
                evt.initEvent('i18n:changed', true, true);
                evt.detail = { lang: current };
                global.dispatchEvent(evt);
            }
        }
        return current;
    }

    function bindLanguageButtons() {
        global.document.querySelectorAll('[data-lang]').forEach(function (el) {
            if (el.hasAttribute('data-lang-bound')) return;
            el.setAttribute('data-lang-bound', 'true');
            el.addEventListener('click', function (e) {
                e.preventDefault();
                setLang(el.getAttribute('data-lang'));
            });
        });
    }

    /* ---------------- 初始化 ---------------- */
    current = detect();
    // 提前把 lang 属性放上去，避免首屏 CSS :lang() 抖动
    try {
        global.document.documentElement.setAttribute('lang', current === 'zh' ? 'zh-CN' : 'en');
    } catch (e) { /* 忽略 */ }

    function boot() {
        applyI18n(global.document);
        bindLanguageButtons();
        // 语言切换后重新绑定（页面若重建了按钮）
        global.addEventListener('i18n:changed', function () {
            bindLanguageButtons();
            applyI18n(global.document);
        });
    }

    if (global.document.readyState === 'loading') {
        global.document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }

    /* ---------------- 导出 ---------------- */
    global.t = t;
    global.localizeServerMessage = localizeServerMessage;
    global.i18n = {
        t: t,
        getLang: getLang,
        setLang: setLang,
        apply: applyI18n,
        localize: localizeServerMessage,
        dict: DICT,
        supported: SUPPORTED.slice()
    };
    // 便于调试时手改
    global.setLang = setLang;
    global.getLang = getLang;
})(window);
