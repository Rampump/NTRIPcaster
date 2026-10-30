// Socket.IO connection configuration
const socket = io({
    timeout: 20000,
    forceNew: false,
    reconnection: true,
    reconnectionDelay: 1000,
    reconnectionDelayMax: 5000,
    maxReconnectionAttempts: 5
});
let currentPage = 'dashboard';
let connectionStatus = 'connecting';

// HTML 转义：所有来自服务端/上行的字符串（用户名、挂载点名、User-Agent、STR 内容）
// 在拼进 innerHTML 之前都必须经过这里，否则构成存储型 XSS。
function escapeHtml(value) {
    if (value === null || value === undefined) return '';
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// WebSocket connection status monitoring
socket.on('connect', function() {
    connectionStatus = 'connected';
    // console.log('WebSocket connection established');
    updateConnectionStatus();
    // If currently on dashboard page, request system statistics
    if (currentPage === 'dashboard') {
        requestSystemStats();
    }
    // 重连后立即重渲染当前页，避免 window.onlineUsers/onlineMounts 停留在断线期间的旧缓存
    if (currentPage === 'users') {
        renderUsersPage();
    } else if (currentPage === 'mounts') {
        renderMountsPage();
    }
});

socket.on('disconnect', function(reason) {
    connectionStatus = 'disconnected';
    // console.log('WebSocket connection disconnected:', reason);
    updateConnectionStatus();
});

socket.on('reconnect', function(attemptNumber) {
    connectionStatus = 'connected';
    // console.log('WebSocket reconnection successful, attempt number:', attemptNumber);
    updateConnectionStatus();
    // 重连成功后立即重渲染当前页，等下一次推送到达前先用现有数据恢复
    if (currentPage === 'users') {
        renderUsersPage();
    } else if (currentPage === 'mounts') {
        renderMountsPage();
    }
});

socket.on('reconnect_attempt', function(attemptNumber) {
    connectionStatus = 'reconnecting';
    // console.log('WebSocket reconnection attempt:', attemptNumber);
    updateConnectionStatus();
});

socket.on('reconnect_failed', function() {
    connectionStatus = 'failed';
    // console.log('WebSocket reconnection failed');
    updateConnectionStatus();
});

// Update connection status display
function updateConnectionStatus() {
    const textKey = {
        'connecting': 'rt.connecting',
        'connected': 'rt.connected',
        'disconnected': 'rt.disconnected',
        'reconnecting': 'rt.reconnecting',
        'failed': 'rt.failed'
    }[connectionStatus];
    const text = textKey ? t(textKey) : t('rt.unknown');

    const label = document.getElementById('realtime-status');
    if (label) {
        label.textContent = text;
        label.classList.toggle('is-offline',
            connectionStatus === 'disconnected' || connectionStatus === 'failed');
    }

    const dot = document.getElementById('realtime-dot');
    if (dot) {
        // 断开后不再呼吸并转红，避免"看着还连着"
        if (connectionStatus === 'connected') {
            dot.classList.remove('waiting');
            dot.style.background = '';
        } else if (connectionStatus === 'connecting' || connectionStatus === 'reconnecting') {
            dot.classList.add('waiting');
            dot.style.background = '';
        } else {
            dot.classList.remove('waiting');
            dot.style.background = 'var(--danger)';
        }
    }
}

// Page navigation
function navigateTo(page) {
    // Check pages that require login
    const requireLoginPages = ['users', 'mounts', 'settings'];
    if (requireLoginPages.includes(page)) {
        // Check login status
        checkLoginStatusForProtectedPage().then(isLoggedIn => {
            if (!isLoggedIn) {
                // Redirect to login page with target page parameter
                window.location.href = `/login?redirect=${page}`;
                return;
            }
            // Logged in, continue navigation
            performNavigation(page);
        });
    } else {
        // Navigate directly for pages that don't require login
        performNavigation(page);
    }
}

// Execute actual page navigation
function performNavigation(page) {
    const previousPage = currentPage;

    // Update navigation state
    document.querySelectorAll('.nav-item').forEach(item => {
        item.classList.remove('active');
    });
    document.querySelector(`[data-page="${page}"]`).classList.add('active');

    currentPage = page;

    // 离开监控页后前端不再消费解析数据，后端也就没必要继续解析：
    // 主动停止并清除按钮高亮，保证「绿色 == 后端正在解析」始终成立。
    if (previousPage === 'monitor' && page !== 'monitor') {
        stopParsingOnLeave();
    }
    
    // Control log panel display
    const logPanel = document.getElementById('log-panel');
    const mainContent = document.querySelector('.main-content');
    
    if (page === 'dashboard') {
        logPanel.style.display = 'block';
        mainContent.classList.add('dashboard-layout');
    } else {
        logPanel.style.display = 'none';
        mainContent.classList.remove('dashboard-layout');
    }
    
    // 每次切换页面重置分页/搜索状态与绑定标记，确保列表从第一页、无过滤重新渲染
    if (page === 'users' || page === 'mounts') {
        delete paginationState[page];
        delete searchState[page];
        delete paginationBound[page === 'users' ? '#users-pagination' : '#mounts-pagination'];
    }

    loadPageContent(page);
}

// Check login status (for protected pages)
async function checkLoginStatusForProtectedPage() {
    try {
        const response = await fetch('/api/users');
        return response.status !== 401;
    } catch (error) {
        // console.error('Failed to check login status:', error);
        return false;
    }
}

// Check login status (original function, maintain compatibility)
async function checkLoginStatus() {
    try {
        const response = await fetch('/api/users');
        if (response.status === 401) {
            showAlert(t('err.loginExpired'), 'warning');
            window.location.href = '/login';
            return false;
        }
        return true;
    } catch (error) {
        // console.error('Failed to check login status:', error);
        return false;
    }
}

// Handle API response
async function handleApiResponse(response, skipAuthRedirect = false) {
    if (response.status === 401) {
        if (!skipAuthRedirect) {
            showAlert(t('err.loginExpired'), 'warning');
            window.location.href = '/login';
        }
        // 用属性标记而不是比对文案：文案会随语言变化，比对字符串一定会漏
        const authError = new Error(t('err.unauthorized'));
        authError.isAuthError = true;
        throw authError;
    }
    
    if (!response.ok) {
        const errorData = await response.json().catch(() => ({ error: t('err.unknown') }));
        throw new Error(localizeServerMessage(errorData.error) || `HTTP ${response.status}`);
    }
    
    return response.json();
}

// Handle API response (for public pages, won't auto-redirect to login)
// Load page content
async function loadPageContent(page) {
    const contentDiv = document.getElementById('page-content');
    
    try {
        let response;
        switch(page) {
            case 'dashboard':
                contentDiv.innerHTML = getDashboardContent();
                // Show content panel for dashboard page
                contentDiv.parentElement.style.display = 'block';
                // Load system statistics
                fetchSystemStats();
                // Request real-time data
                requestSystemStats();
                break;
            case 'users':
                // Ensure content panel is displayed on non-dashboard pages
                contentDiv.parentElement.style.display = 'block';
                response = await fetch('/api/users');
                const users = await handleApiResponse(response);
                // /api/users API already contains correct online status information, use directly
                contentDiv.innerHTML = getUsersContent(users);
                // 初始化分页渲染（首屏）
                renderUsersPage();
                // 绑定搜索过滤
                bindSearchEvents('users', renderUsersPage);
                break;
            case 'mounts':
                // Ensure content panel is displayed on non-dashboard pages
                contentDiv.parentElement.style.display = 'block';
                response = await fetch('/api/mounts');
                const mounts = await handleApiResponse(response);
                // /api/mounts API already contains correct online status and connection count information, use directly
                contentDiv.innerHTML = getMountsContent(mounts);
                // 初始化分页渲染（首屏）
                renderMountsPage();
                // 绑定搜索过滤
                bindSearchEvents('mounts', renderMountsPage);
                break;
            case 'monitor':
                // Ensure content panel is displayed on non-dashboard pages
                contentDiv.parentElement.style.display = 'block';
                contentDiv.innerHTML = getMonitorContent();
                // Update monitoring data display immediately
                updateMonitorData();
                // Add INFO button event handling for STR items
                setTimeout(() => {
                    addInfoButtonsToSTRItems();
                }, 200);
                // Initialize map when monitor page is loaded
                setTimeout(() => {
                    initializeMapForMonitor();
                }, 300);
                // 进入监控页：开始校验后端解析状态，让按钮绿色与真实解析状态一致
                startParsingSync();
                break;
            case 'settings':
                // Ensure content panel is displayed on non-dashboard pages
                contentDiv.parentElement.style.display = 'block';
                contentDiv.innerHTML = getSettingsContent();
                break;
        }
    } catch (error) {
        // console.error('Failed to load page content:', error);
        contentDiv.innerHTML = `<div class="error-message">${escapeHtml(t('dash.loadFailed'))}</div>`;
    }
}

// Add INFO button event handling for STR items
// 按钮绿色的判定规则：绿色 == 后端此刻正在解析该挂载点并向前端推送数据。
// 所以绿色不能跟「用户点过谁」走，必须跟后端 /api/mount/rtcm-parse/status 的
// current_web_mount 走——后台停止解析后，按钮就要恢复本色。
// 之前的实现只记点击过的挂载点，切页/刷新后后台已经停了，绿色却还留着。
let currentSelectedMount = null;   // 用户最近点击的挂载点（仅用于点击瞬间的乐观反馈）
let activeParsingMount = null;     // 后端确实在推送数据的挂载点（决定绿色）
let parsingSyncTimer = null;       // 周期校验定时器
let parsingSyncInFlight = false;   // 防止请求堆积

function addInfoButtonsToSTRItems() {
    const infoButtons = document.querySelectorAll('.str-info-btn');
    
    infoButtons.forEach(button => {
        // Avoid duplicate event binding
        if (button.hasAttribute('data-event-bound')) {
            return;
        }
        
        const mountName = button.getAttribute('data-mount');
        if (!mountName) {
            return;
        }
        
        button.title = t('monitor.infoTitle', { name: mountName });
        
        button.addEventListener('click', () => {
            // 记录当前选中的挂载点并高亮对应按钮
            currentSelectedMount = mountName;
            // 乐观高亮：点击立刻给反馈，随后由后端状态校正（启动失败会自动撤销）
            activeParsingMount = mountName;
            applyMountButtonHighlight();
            
            // Start RTCM parsing and update container content
            startRTCMParsing(mountName);
        });
        
        // Mark as event bound
        button.setAttribute('data-event-bound', 'true');
    });
    
    // 重渲染后恢复选中态（避免 socket 推送重绘导致绿字丢失）
    applyMountButtonHighlight();
}

// 高亮「后端正在解析」的挂载点按钮：选中的变绿（--success），其余恢复原色
function applyMountButtonHighlight() {
    const infoButtons = document.querySelectorAll('.str-info-btn');
    infoButtons.forEach(button => {
        const mountName = button.getAttribute('data-mount');
        if (mountName && mountName === activeParsingMount) {
            button.classList.add('active');
        } else {
            button.classList.remove('active');
        }
    });
}

// 从后端同步「正在解析」的挂载点并刷新按钮颜色。
// 这是绿色的唯一依据：后端 current_web_mount 为空，说明解析已停，按钮必须复原色。
// 覆盖的时机包括：刷新页面、后端因 WebSocket 断开自动清理、其他页面/用户切换挂载点。
function syncParsingHighlight() {
    if (parsingSyncInFlight) return Promise.resolve();
    parsingSyncInFlight = true;
    return fetch('/api/mount/rtcm-parse/status')
        .then(response => response.json())
        .then(res => {
            if (!res || !res.success || !res.status) return;
            const current = res.status.current_web_mount || null;
            if (current !== activeParsingMount) {
                activeParsingMount = current;
                currentSelectedMount = current;
                applyMountButtonHighlight();
            }
        })
        .catch(() => {})
        .finally(() => { parsingSyncInFlight = false; });
}

// 进入监控页时开始周期校验（5 秒一次，单个轻量 GET）
function startParsingSync() {
    stopParsingSync();
    parsingSyncTimer = setInterval(syncParsingHighlight, 5000);
    syncParsingHighlight();
}

function stopParsingSync() {
    if (parsingSyncTimer) {
        clearInterval(parsingSyncTimer);
        parsingSyncTimer = null;
    }
}

// 离开监控页：通知后端停止解析，并立即清除高亮。
// 不做这一步就会出现「后台已经停了、按钮还绿着」的错位；
// stop 接口本身是全局停止（与 WebSocket 断开时的清理逻辑一致）。
function stopParsingOnLeave() {
    stopParsingSync();
    const had = activeParsingMount;
    activeParsingMount = null;
    currentSelectedMount = null;
    applyMountButtonHighlight();
    if (!had) return;
    fetch('/api/mount/rtcm-parse/stop', { method: 'POST' }).catch(() => {});
}

// Start RTCM parsing
// Store last position information for position change detection
let lastPosition = { latitude: null, longitude: null };
// Store map center for distance comparison
let mapCenter = { latitude: null, longitude: null };
// Track if this is the first marking
let isFirstMarking = true;
// Track if map was switched to force re-marking
let mapSwitched = false;
// Store current mount name for map display
let currentMountName = null;

function startRTCMParsing(mountName) {
    console.log(`[前端] 开始启动RTCM解析: ${mountName}`);
    
    // 
    fetch('/api/mount/rtcm-parse/status')
    .then(response => response.json())
    .then(statusData => {
        if (statusData.success) {
            const status = statusData.status;
            console.log(`[前端] 当前解析器状态:`, status);
            console.log(`[前端] 当前活跃Web挂载点: ${status.current_web_mount || '无'}`);
            console.log(`[前端] Web解析线程数: ${status.web_parsers}, STR解析线程数: ${status.str_parsers}`);
            
            if (status.current_web_mount && status.current_web_mount !== mountName) {
                console.log(`[前端] 检测到前一个活跃挂载点: ${status.current_web_mount}，将被自动清理`);
            }
        }
    })
    .catch(error => {
        console.warn(`[前端] 获取解析器状态失败:`, error);
    });
    
    // Reset marking status for new mount point
    isFirstMarking = true;
    mapSwitched = false;
    lastPosition = { latitude: null, longitude: null };
    mapCenter = { latitude: null, longitude: null };
    
    // Update base station information container
    updateStationInfo(mountName);
    
    // Initialize satellite visualization
    initializeSatelliteVisualization();
    
    // Call backend API to start RTCM parsing
    console.log(`[前端] 调用后端API启动RTCM解析: ${mountName}`);
    fetch(`/api/mount/${mountName}/rtcm-parse/start`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            console.log(`[前端] RTCM解析启动成功: ${mountName}`);
            // 启动成功后用后端真实状态校正按钮高亮（避免乐观高亮与实际不符）
            setTimeout(syncParsingHighlight, 600);
            // 
            setTimeout(() => {
                fetch('/api/mount/rtcm-parse/status')
                .then(response => response.json())
                .then(statusData => {
                    if (statusData.success) {
                        console.log(`[前端] 启动后解析器状态:`, statusData.status);
                    }
                })
                .catch(error => console.warn(`[前端] 获取启动后状态失败:`, error));
            }, 1000);
        } else {
            console.error(`[前端] RTCM解析启动失败: ${data.error || 'Unknown error'}`);
            // 启动失败（例如站点离线）：撤销乐观高亮，避免按钮绿着但后台没在解析
            if (activeParsingMount === mountName) {
                activeParsingMount = null;
                currentSelectedMount = null;
                applyMountButtonHighlight();
            }
            showAlert(t('monitor.rtcmStartFailed', {
                error: localizeServerMessage(data.error) || t('err.unknown')
            }), 'error');
        }
    })
    .catch(error => {
        console.error('[前端] 调用RTCM解析API失败:', error);
        // 请求失败：同样撤销乐观高亮
        if (activeParsingMount === mountName) {
            activeParsingMount = null;
            currentSelectedMount = null;
            applyMountButtonHighlight();
        }
        showAlert(t('monitor.rtcmApiFailed'), 'error');
    });
}

// Calculate distance between two points (meters)
function calculateDistance(lat1, lon1, lat2, lon2) {
    const R = 6371000; // Earth radius (meters)
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLon = (lon2 - lon1) * Math.PI / 180;
    const a = Math.sin(dLat/2) * Math.sin(dLat/2) +
              Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
              Math.sin(dLon/2) * Math.sin(dLon/2);
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
    return R * c;
}

// Handle position updates, determine if re-marking is needed
function handlePositionUpdate(latitude, longitude, mountName = null) {
    // Force re-marking if map was switched
    if (mapSwitched) {
        mapSwitched = false;
        lastPosition.latitude = latitude;
        lastPosition.longitude = longitude;
        updateMapLocation(latitude, longitude, mountName, false); // Second marking, don't fix zoom
        return;
    }
    
    // First time marking - always mark and set zoom to 8
    if (isFirstMarking) {
        isFirstMarking = false;
        lastPosition.latitude = latitude;
        lastPosition.longitude = longitude;
        mapCenter.latitude = latitude;
        mapCenter.longitude = longitude;
        updateMapLocation(latitude, longitude, mountName, true); // First marking, set zoom to 8
        return;
    }
    
    // Check both distance conditions - update marker if either condition is met
    let shouldUpdateMarker = false;
    let updateReason = '';
    
    // Check distance from last position (500m threshold)
    if (lastPosition.latitude !== null && lastPosition.longitude !== null) {
        const distance = calculateDistance(
            lastPosition.latitude, lastPosition.longitude,
            latitude, longitude
        );
        
        // If position change is 500 meters or more, should update marker
        if (distance >= 500) {
            shouldUpdateMarker = true;
            updateReason = `position change ${distance.toFixed(1)}m >= 500m threshold`;
        }
    }
    
    // Check distance from map center (50km threshold)
    if (mapCenter.latitude !== null && mapCenter.longitude !== null) {
        const centerDistance = calculateDistance(
            mapCenter.latitude, mapCenter.longitude,
            latitude, longitude
        );
        
        // If distance from map center is 50km or more, should update marker
        if (centerDistance >= 50000) {
            shouldUpdateMarker = true;
            updateReason = `distance from map center ${(centerDistance/1000).toFixed(1)}km >= 50km threshold`;
            // Update map center when re-marking due to distance
            mapCenter.latitude = latitude;
            mapCenter.longitude = longitude;
        }
    }
    
    // Check if marker is visible in current map view
    if (currentMap && !shouldUpdateMarker) {
        const view = currentMap.getView();
        const extent = view.calculateExtent(currentMap.getSize());
        const markerCoord = ol.proj.fromLonLat([longitude, latitude]);
        
        // If marker is not within current view extent, should update marker
        if (!ol.extent.containsCoordinate(extent, markerCoord)) {
            shouldUpdateMarker = true;
            updateReason = 'marker not visible in current map view';
            // Update map center to current marker position
            const currentCenter = ol.proj.toLonLat(view.getCenter());
            mapCenter.latitude = currentCenter[1];
            mapCenter.longitude = currentCenter[0];
        }
    }
    
    // If neither condition is met, don't update marker
    if (!shouldUpdateMarker) {
        // console.log(`No update needed - position and center distance within thresholds`);
        return;
    }
    
    // console.log(`Updating marker: ${updateReason}`);
    
    // Update position and mark
    lastPosition.latitude = latitude;
    lastPosition.longitude = longitude;
    updateMapLocation(latitude, longitude, mountName, false); // Subsequent marking, don't fix zoom
}

// Update base station information
function updateStationInfo(mountName) {
    const stationInfoDiv = document.getElementById('station-info');
    stationInfoDiv.innerHTML = `
        <div class="station-info-loading">
            <p>${escapeHtml(t('monitor.rtcmParsing', { name: mountName }))}</p>
            <div class="loading-spinner"></div>
        </div>
    `;
    
    // Base station information is now displayed through simulated data
}

// Display base station information
function displayStationInfo(stationData) {
    const stationInfoDiv = document.getElementById('station-info');
    const unknown = escapeHtml(t('common.unknown'));
    stationInfoDiv.innerHTML = `
        <div class="station-details">
            <!-- 第一行：基本信息 -->
            <div class="info-row-group">
                <div class="info-row">
                    <span class="info-label">${escapeHtml(t('station.mount'))}</span>
                    <span class="info-value" id="station-name">${escapeHtml(stationData.name || t('common.unknown'))}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">${escapeHtml(t('station.id'))}</span>
                    <span class="info-value" id="station-id">${escapeHtml(stationData.id || t('common.unknown'))}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">${escapeHtml(t('station.country'))}</span>
                    <span class="info-value" id="station-country">${escapeHtml(stationData.country_name || stationData.country || t('common.unknown'))}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">${escapeHtml(t('station.city'))}</span>
                    <span class="info-value" id="station-city">${escapeHtml(stationData.city || t('common.unknown'))}</span>
                </div>
            </div>
            
            <!-- 第二行：设备信息 -->
            <div class="info-row-group">
                <div class="info-row">
                    <span class="info-label">${escapeHtml(t('station.receiverType'))}</span>
                    <span class="info-value" id="receiver-type">${escapeHtml(stationData.receiver?.name || t('common.unknown'))}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">${escapeHtml(t('station.receiverFw'))}</span>
                    <span class="info-value" id="receiver-version">${escapeHtml(stationData.receiver?.firmware || t('common.unknown'))}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">${escapeHtml(t('station.antennaType'))}</span>
                    <span class="info-value" id="antenna-type">${escapeHtml(stationData.antenna?.name || t('common.unknown'))}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">${escapeHtml(t('station.antennaSerial'))}</span>
                    <span class="info-value" id="antenna-serial">${escapeHtml(stationData.antenna?.serial || t('common.unknown'))}</span>
                </div>
            </div>
            
            <!-- 第三行：坐标信息 -->
            <div class="info-row-group coordinates-group">
                <div class="coordinates-half">
                    <div class="info-row">
                        <span class="info-label">${escapeHtml(t('station.coordinates'))}</span>
                        <span class="info-value">${escapeHtml(t('station.longitude'))} <span id="station-longitude">${stationData.longitude || 0}</span>°, ${escapeHtml(t('station.latitude'))} <span id="station-latitude">${stationData.latitude || 0}</span>°, ${escapeHtml(t('station.height'))} <span id="station-height">${escapeHtml(String(stationData.height || unknown))}</span></span>
                    </div>
                </div>
                <div class="coordinates-half">
                    <div class="info-row">
                        <span class="info-label">${escapeHtml(t('station.ecef'))}</span>
                        <span class="info-value" id="station-xyz">X: ${stationData.x || 0}, Y: ${stationData.y || 0}, Z: ${stationData.z || 0}</span>
                    </div>
                </div>
            </div>
        </div>
    `;
    
    // Update base station status
    updateStationStatus(true);
    
    // Hide map loading overlay
    const mapOverlay = document.getElementById('map-loading');
    if (mapOverlay) {
        mapOverlay.style.display = 'none';
    }
}

// Map related variables
let currentMap = null;
let mapType = 'amap'; // 'amap' or 'osm'
let stationMarker = null;
let coverageCircles = [];

// Initialize map
function initializeMap() {
    // Set map switch button events
    const amapBtn = document.getElementById('amap-btn');
    const osmBtn = document.getElementById('osm-btn');
    
    amapBtn.addEventListener('click', () => switchToAmap());
    osmBtn.addEventListener('click', () => switchToOSM());
    
    // Load map library by default
    loadMapLibrary();
    
    // Position data is now updated through simulated data
}

// Initialize map specifically for monitor page
function initializeMapForMonitor() {
    console.log('[地图初始化] 开始初始化monitor页面地图');
    
    // Check if we're on monitor page and map container exists
    if (currentPage !== 'monitor') {
        console.log('[地图初始化] 不在monitor页面，跳过地图初始化');
        return;
    }
    
    const mapContainer = document.getElementById('map');
    if (!mapContainer) {
        console.log('[地图初始化] 地图容器不存在，跳过初始化');
        return;
    }
    
    // Set map switch button events (re-bind after page reload)
    const amapBtn = document.getElementById('amap-btn');
    const osmBtn = document.getElementById('osm-btn');
    
    if (amapBtn && osmBtn) {
        // Remove existing event listeners to avoid duplicates
        amapBtn.replaceWith(amapBtn.cloneNode(true));
        osmBtn.replaceWith(osmBtn.cloneNode(true));
        
        // Re-get elements after replacement
        const newAmapBtn = document.getElementById('amap-btn');
        const newOsmBtn = document.getElementById('osm-btn');
        
        newAmapBtn.addEventListener('click', () => switchToAmap());
        newOsmBtn.addEventListener('click', () => switchToOSM());
        
        console.log('[地图初始化] 地图切换按钮事件已重新绑定');
    }
    
    // Force re-initialize map
    if (typeof ol !== 'undefined') {
        console.log('[地图初始化] OpenLayers已加载，直接初始化地图');
        initMap();
        
        // If we have previous position data, restore the marker
        if (lastPosition.latitude !== null && lastPosition.longitude !== null) {
            console.log('[地图初始化] 恢复之前的位置标记:', lastPosition);
            // Force re-marking without distance check
            updateMapLocation(lastPosition.latitude, lastPosition.longitude, currentMountName, false);
        }
    } else {
        console.log('[地图初始化] OpenLayers未加载，开始加载库');
        loadMapLibrary();
    }
}

// Load OpenLayers map library
function loadMapLibrary() {
    if (typeof ol === 'undefined') {
        // Dynamically load OpenLayers CSS
        const link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = 'https://cdn.jsdelivr.net/npm/ol@v7.5.2/ol.css';
        document.head.appendChild(link);
        
        // Dynamically load OpenLayers JS
        const script = document.createElement('script');
        script.src = 'https://cdn.jsdelivr.net/npm/ol@v7.5.2/dist/ol.js';
        script.onload = () => initMap();
        document.head.appendChild(script);
    } else {
        initMap();
    }
}

// Initialize map
function initMap() {
    console.log('[地图初始化] 开始创建地图实例');
    
    // Check if map container exists
    const mapContainer = document.getElementById('map');
    if (!mapContainer) {
        console.log('[地图初始化] 地图容器不存在，无法创建地图');
        return;
    }
    
    // Clean up existing map
    if (currentMap) {
        console.log('[地图初始化] 清理现有地图实例');
        currentMap.setTarget(null);
        currentMap = null;
    }
    
    // Create layer based on current map type
    const layer = mapType === 'amap' ? createAmapLayer() : createOSMLayer();
    
    try {
        currentMap = new ol.Map({
            target: 'map',
            layers: [layer],
            view: new ol.View({
                center: ol.proj.fromLonLat([110.277492, 25.20341154]),
                zoom: 8
            })
        });
        
        // Create marker layer
        const markerLayer = new ol.layer.Vector({
            source: new ol.source.Vector()
        });
        currentMap.addLayer(markerLayer);
        
        console.log('[地图初始化] 地图实例创建成功');
        updateMapButtons();
    } catch (error) {
        console.error('[地图初始化] 创建地图实例失败:', error);
    }
}

// Create Amap layer
function createAmapLayer() {
    return new ol.layer.Tile({
        source: new ol.source.XYZ({
            url: 'https://wprd01.is.autonavi.com/appmaptile?x={x}&y={y}&z={z}&lang=zh_cn&size=1&scl=1&style=7',
            crossOrigin: 'anonymous'
        })
    });
}

// Create OSM layer
function createOSMLayer() {
    return new ol.layer.Tile({
        source: new ol.source.OSM()
    });
}

// Switch to Amap
function switchToAmap() {
    if (mapType !== 'amap') {
        mapType = 'amap';
        mapSwitched = true; // Mark that map was switched
        initMap();
        // If we have position data, force re-marking
        if (lastPosition.latitude !== null && lastPosition.longitude !== null) {
            handlePositionUpdate(lastPosition.latitude, lastPosition.longitude);
        }
    }
}

// Switch to OSM
function switchToOSM() {
    if (mapType !== 'osm') {
        mapType = 'osm';
        mapSwitched = true; // Mark that map was switched
        initMap();
        // If we have position data, force re-marking
        if (lastPosition.latitude !== null && lastPosition.longitude !== null) {
            handlePositionUpdate(lastPosition.latitude, lastPosition.longitude);
        }
    }
}

// Update map button status
function updateMapButtons() {
    const amapBtn = document.getElementById('amap-btn');
    const osmBtn = document.getElementById('osm-btn');
    
    if (mapType === 'amap') {
        amapBtn.className = 'btn btn-primary btn-sm';
        osmBtn.className = 'btn btn-secondary btn-sm';
    } else {
        amapBtn.className = 'btn btn-secondary btn-sm';
        osmBtn.className = 'btn btn-primary btn-sm';
    }
}


// 坐标转换函数：WGS84转GCJ02（火星坐标系）
function wgs84ToGcj02(lng, lat) {
    const x_pi = 3.14159265358979324 * 3000.0 / 180.0;
    const pi = 3.1415926535897932384626;
    const a = 6378245.0; // 长半轴
    const ee = 0.00669342162296594323; // 扁率
    
    // 判断是否在中国境外
    function outOfChina(lng, lat) {
        return (lng < 72.004 || lng > 137.8347) || (lat < 0.8293 || lat > 55.8271);
    }
    
    function transformLat(lng, lat) {
        let ret = -100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat + 0.1 * lng * lat + 0.2 * Math.sqrt(Math.abs(lng));
        ret += (20.0 * Math.sin(6.0 * lng * pi) + 20.0 * Math.sin(2.0 * lng * pi)) * 2.0 / 3.0;
        ret += (20.0 * Math.sin(lat * pi) + 40.0 * Math.sin(lat / 3.0 * pi)) * 2.0 / 3.0;
        ret += (160.0 * Math.sin(lat / 12.0 * pi) + 320 * Math.sin(lat * pi / 30.0)) * 2.0 / 3.0;
        return ret;
    }
    
    function transformLng(lng, lat) {
        let ret = 300.0 + lng + 2.0 * lat + 0.1 * lng * lng + 0.1 * lng * lat + 0.1 * Math.sqrt(Math.abs(lng));
        ret += (20.0 * Math.sin(6.0 * lng * pi) + 20.0 * Math.sin(2.0 * lng * pi)) * 2.0 / 3.0;
        ret += (20.0 * Math.sin(lng * pi) + 40.0 * Math.sin(lng / 3.0 * pi)) * 2.0 / 3.0;
        ret += (150.0 * Math.sin(lng / 12.0 * pi) + 300.0 * Math.sin(lng / 30.0 * pi)) * 2.0 / 3.0;
        return ret;
    }
    
    // 如果在中国境外，不进行转换
    if (outOfChina(lng, lat)) {
        return [lng, lat];
    }
    
    let dlat = transformLat(lng - 105.0, lat - 35.0);
    let dlng = transformLng(lng - 105.0, lat - 35.0);
    const radlat = lat / 180.0 * pi;
    let magic = Math.sin(radlat);
    magic = 1 - ee * magic * magic;
    const sqrtmagic = Math.sqrt(magic);
    dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sqrtmagic) * pi);
    dlng = (dlng * 180.0) / (a / sqrtmagic * Math.cos(radlat) * pi);
    const mglat = lat + dlat;
    const mglng = lng + dlng;
    return [mglng, mglat];
}

function updateMapLocation(latitude, longitude, mountName = null, isInitialMarking = false) {
    if (!currentMap) return;
    
    // 根据地图类型决定是否进行坐标转换
    let displayLng = longitude;
    let displayLat = latitude;
    
    // 如果是高德地图，需要将WGS84坐标转换为GCJ02坐标
    if (mapType === 'amap') {
        const converted = wgs84ToGcj02(longitude, latitude);
        displayLng = converted[0];
        displayLat = converted[1];
        console.log(`[坐标转换] WGS84: ${longitude}, ${latitude} -> GCJ02: ${displayLng}, ${displayLat}`);
    }
    
    const center = ol.proj.fromLonLat([displayLng, displayLat]);
    currentMap.getView().setCenter(center);
    
    
    if (isInitialMarking) {
        currentMap.getView().setZoom(8);
    }
    
    
    const layers = currentMap.getLayers().getArray();
    const markerLayer = layers.find(layer => layer instanceof ol.layer.Vector);
    
    if (markerLayer) {
        const source = markerLayer.getSource();
        
        
        source.clear();
        
        
        const markerFeature = new ol.Feature({
            geometry: new ol.geom.Point(center),
            name: 'Base Station Location'
        });
        
        markerFeature.setStyle(new ol.style.Style({
            image: new ol.style.Icon({
                src: 'data:image/svg+xml;base64,' + btoa(`
                    <svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 40 40">
                        <defs>
                            <filter id="shadow" x="-50%" y="-50%" width="200%" height="200%">
                                <feDropShadow dx="1.5" dy="2.5" stdDeviation="2" flood-color="rgba(0,0,0,0.35)"/>
                            </filter>
                            <linearGradient id="towerGrad" x1="0" y1="0" x2="0" y2="1">
                                <stop offset="0%" stop-color="#42A5F5"/>
                                <stop offset="100%" stop-color="#1565C0"/>
                            </linearGradient>
                        </defs>
                        <!-- 底座 -->
                        <rect x="13" y="32" width="14" height="3" rx="1.5" fill="#0D47A1"/>
                        <rect x="15" y="28" width="10" height="4" rx="1.5" fill="#0D47A1"/>
                        <!-- 塔身 -->
                        <path d="M20 5 L24 28 L16 28 Z" fill="url(#towerGrad)" stroke="#0D47A1" stroke-width="1"/>
                        <!-- 顶部圆点（信号源） -->
                        <circle cx="20" cy="5" r="4" fill="#FFC107" stroke="#0D47A1" stroke-width="1.2" filter="url(#shadow)"/>
                        <!-- 信号波纹 -->
                        <path d="M20 2 C24 -2 32 -2 34 4" fill="none" stroke="#FF7043" stroke-width="1.6" stroke-linecap="round"/>
                        <path d="M20 3 C25 -4 36 -4 39 3" fill="none" stroke="#FF7043" stroke-width="1.4" stroke-linecap="round" opacity="0.6"/>
                        <!-- 横梁 -->
                        <rect x="14" y="18" width="12" height="1.8" rx="0.9" fill="#0D47A1"/>
                        <rect x="15" y="12" width="10" height="1.6" rx="0.8" fill="#0D47A1"/>
                    </svg>
                `),
                scale: 1,
                anchor: [0.5, 1.0]
            })
        }));
        
        source.addFeature(markerFeature);
        
        
        if (mountName) {
            const textFeature = new ol.Feature({
                geometry: new ol.geom.Point(center),
                name: 'Mount Name Label'
            });
            
            textFeature.setStyle(new ol.style.Style({
                text: new ol.style.Text({
                    text: mountName,
                    font: 'bold 18px Arial',
                    fill: new ol.style.Fill({
                        color: '#1565C0'
                    }),
                    stroke: new ol.style.Stroke({
                        color: '#FFFFFF',
                        width: 3
                    }),
                    offsetY: -25,
                    textAlign: 'center',
                    textBaseline: 'bottom'
                })
            }));
            
            source.addFeature(textFeature);
        }
        
        
        const circle20km = new ol.Feature({
            geometry: new ol.geom.Circle(center, 20000)
        });
        
        circle20km.setStyle(new ol.style.Style({
            fill: new ol.style.Fill({
                color: 'rgba(21, 101, 192, 0.15)'
            })
        }));
        
        source.addFeature(circle20km);
        
        
        const circle50km = new ol.Feature({
            geometry: new ol.geom.Circle(center, 50000)
        });
        
        circle50km.setStyle(new ol.style.Style({
            fill: new ol.style.Fill({
                color: 'rgba(66, 165, 245, 0.2)'
            })
        }));
        
        source.addFeature(circle50km);
    }
}

// SATELLITE
let satelliteContainers = {};
let satelliteData = {};
let frequencyMap = {};

// freq_map
async function loadFrequencyMap() {
    try {
        const response = await fetch('/static/freq_map.json');
        frequencyMap = await response.json();
        // console.log('频率映射表加载成功');
    } catch (error) {
        // console.error('频率映射表加载失败:', error);
    }
}




function getFrequencyInfo(constellation, channel) {
    
    const constellationMap = {
        'GPS': 'GPS',
        'GLONASS': 'GLO', 
        'GALILEO': 'GAL',
        'BDS': 'BDS',
        'QZSS': 'QZS',
        'SBAS': 'SBAS',
        'IRNSS': 'IRN',
        'NAVIC': 'NAV'
    };
    
    const mappedConstellation = constellationMap[constellation];
    if (!mappedConstellation || !frequencyMap[mappedConstellation] || !channel) {
        return { band: t('common.unknown'), freq: t('common.unknown') };
    }
    
    const freqInfo = frequencyMap[mappedConstellation][channel];
    return freqInfo || { band: t('common.unknown'), freq: t('common.unknown') };
}


function initializeSatelliteVisualization() {
    const satelliteContainer = document.getElementById('satellite-container');
    if (!satelliteContainer) {
        // console.warn('卫星容器不存在，无法初始化卫星可视化');
        return;
    }
    
    
    satelliteContainer.innerHTML = '';
    
    
    const supportedConstellations = ['GPS', 'GLONASS', 'GALILEO', 'BDS', 'QZSS', 'SBAS', 'IRNSS', 'NAVIC'];
    supportedConstellations.forEach(constellation => {
        createConstellationContainer(constellation);
        
        const constellationContainer = document.querySelector(`#chart-${constellation}`).closest('.constellation-container');
        if (constellationContainer) {
            constellationContainer.style.display = 'none';
        }
    });
    
    // console.log('卫星可视化初始化完成，已创建', supportedConstellations.length, '个星座容器（初始隐藏状态）');
}

 
function updateSatelliteVisualization(constellation, satellites) {
     
    satelliteData[constellation] = satellites;
    
    
    updateSatelliteStatus(satellites && satellites.length > 0);
    
     
    updateConstellationChart(constellation, satellites);
}


function createConstellationContainer(constellation) {
    const satelliteContainer = document.getElementById('satellite-container');
    
    const constellationDiv = document.createElement('div');
    constellationDiv.className = 'constellation-container';
    constellationDiv.id = `constellation-${constellation}`;
    
    constellationDiv.innerHTML = `
        <h5 class="constellation-title">${escapeHtml(constellation)}</h5>
        <div class="satellite-chart" id="chart-${constellation}"></div>
    `;
    
    satelliteContainer.appendChild(constellationDiv);
    satelliteContainers[constellation] = constellationDiv;
}


function updateConstellationChart(constellation, satellites) {
    const chartContainer = document.getElementById(`chart-${constellation}`);
    if (!chartContainer) {
        // console.warn(`图表容器 chart-${constellation} 不存在`);
        return;
    }
    
     
    const currentTime = Date.now();
    
    
    if (!satelliteData[constellation]) {
        satelliteData[constellation] = {};
    }
    
    
    satellites.forEach(satellite => {
        satelliteData[constellation][satellite.name] = {
            ...satellite,
            lastUpdate: currentTime
        };
    });
    
    
    const expireTime = 10000; // 10秒
    Object.keys(satelliteData[constellation]).forEach(satName => {
        if (currentTime - satelliteData[constellation][satName].lastUpdate > expireTime) {
            delete satelliteData[constellation][satName];
        }
    });
    
    
    const constellationContainer = chartContainer.closest('.constellation-container');
    const activeSatelliteCount = Object.keys(satelliteData[constellation]).length;
    
    
    if (activeSatelliteCount === 0) {
        
        if (constellationContainer) {
            constellationContainer.style.display = 'none';
        }
        // console.log(`${constellation} 星座模块已隐藏（无数据）`);
        return;
    } else {
        
        if (constellationContainer) {
            constellationContainer.style.display = 'block';
        }
    }
    
    
    chartContainer.innerHTML = '';
    
    const activeSatellites = Object.values(satelliteData[constellation]);
    
    // 柱子统一由 .satellite-chart 的 flex 布局横向排列，这里不再用 inline-block / marginRight，
    // 避免卫星被逐行换行堆成竖排、把页面拉长。
    const barWidth = 30;
    
    activeSatellites.forEach(satellite => {
        const barContainer = document.createElement('div');
        barContainer.className = 'satellite-bar-container';
        
        const bar = document.createElement('div');
        bar.className = 'satellite-bar';
        bar.style.height = `${Math.max(satellite.signalStrength * 2, 10)}px`;
        bar.style.backgroundColor = getSignalColor(satellite.signalStrength);
        bar.style.width = `${barWidth}px`;
        
        const label = document.createElement('div');
        label.className = 'satellite-label';
        label.textContent = satellite.name;
        label.style.width = `${barWidth}px`;
        
        const strength = document.createElement('div');
        strength.className = 'satellite-strength';
        strength.textContent = satellite.signalStrength;
        strength.style.width = `${barWidth}px`;
        
        
        barContainer.addEventListener('mouseenter', (e) => {
            showSatelliteTooltip(e, satellite, constellation);
        });
        
        barContainer.addEventListener('mouseleave', () => {
            
            tooltipHideTimeout = setTimeout(() => {
                hideSatelliteTooltip();
            }, 300);
        });
        
        
        barContainer.addEventListener('mousemove', (e) => {
            updateTooltipPosition(e);
        });
        
        barContainer.appendChild(strength);
        barContainer.appendChild(bar);
        barContainer.appendChild(label);
        chartContainer.appendChild(barContainer);
    });
    
    
}


function getSignalColor(strength) {
    if (strength >= 40) return '#4CAF50'; // 绿色 Green
    if (strength >= 30) return '#FFC107'; // 黄色 Yellow
    if (strength >= 20) return '#FF9800'; // 橙色 Orange 颜色好像不对~
    return '#F44336'; // 红色 Red
}

let currentTooltip = null;
let tooltipHideTimeout = null;


function showSatelliteTooltip(event, satellite, constellation) {
    
    if (tooltipHideTimeout) {
        clearTimeout(tooltipHideTimeout);
        tooltipHideTimeout = null;
    }
    
    
    hideSatelliteTooltip();
    
    
    const freqInfo = getFrequencyInfo(constellation, satellite.channel);
    
    const tooltip = document.createElement('div');
    tooltip.className = 'satellite-tooltip';
    tooltip.innerHTML = `
        <div><strong>${escapeHtml(satellite.name)}</strong></div>
        <div>${escapeHtml(t('sat.signal'))} ${satellite.signalStrength} dBHz</div>
                    <div>${escapeHtml(t('sat.elevation'))} ${satellite.elevation}°</div>
                    <div>${escapeHtml(t('sat.azimuth'))} ${satellite.azimuth}°</div>
                    <div>${escapeHtml(t('sat.band'))} ${freqInfo.band}</div>
                    <div>${escapeHtml(t('sat.frequency'))} ${freqInfo.freq}</div>
                    <div>${escapeHtml(t('sat.channel'))} ${escapeHtml(satellite.channel || t('common.unknown'))}</div>
    `;
    
    tooltip.style.cssText = `
        position: absolute;
        background: rgba(0, 0, 0, 0.9);
        color: white;
        padding: 10px;
        border-radius: 5px;
        font-size: 12px;
        z-index: 10000;
        pointer-events: none;
        box-shadow: 0 2px 10px rgba(0,0,0,0.3);
        max-width: 200px;
        transition: opacity 0.2s ease;
    `;
    
    document.body.appendChild(tooltip);
    currentTooltip = tooltip;
    
    
    updateTooltipPosition(event);
}


function updateTooltipPosition(event) {
    if (!currentTooltip) return;
    
    const tooltip = currentTooltip;
    
    
    let left = event.pageX + 10;
    let top = event.pageY - 10;
    
    
    if (left + tooltip.offsetWidth > window.innerWidth + window.scrollX) {
        left = event.pageX - tooltip.offsetWidth - 10;
    }
    
    
    if (top < window.scrollY) {
        top = event.pageY + 20;
    }
    
    
    if (top + tooltip.offsetHeight > window.innerHeight + window.scrollY) {
        top = event.pageY - tooltip.offsetHeight - 10;
    }
    
    tooltip.style.left = left + 'px';
    tooltip.style.top = top + 'px';
}


function hideSatelliteTooltip() {
    if (currentTooltip) {
        currentTooltip.remove();
        currentTooltip = null;
    }
    if (tooltipHideTimeout) {
        clearTimeout(tooltipHideTimeout);
        tooltipHideTimeout = null;
    }
}


function getDashboardContent() {
    const L = (key) => escapeHtml(t(key));
    return `
        <div class="page-header">
            <h3>${L('dash.title')}</h3>
            <div class="dashboard-timestamp" id="dashboard-timestamp">${L('common.loading')}</div>
        </div>

        <div class="dashboard-cards">
            <div class="dashboard-card">
                <div class="card-icon"><i class="fas fa-clock"></i></div>
                <div class="card-content">
                    <div class="card-title">${L('dash.uptime')}</div>
                    <div class="card-value" id="system-uptime">–</div>
                </div>
            </div>

            <div class="dashboard-card">
                <div class="card-icon"><i class="fas fa-broadcast-tower"></i></div>
                <div class="card-content">
                    <div class="card-title">${L('dash.onlineMounts')}</div>
                    <div class="card-value" id="dashboard-active-mounts">–</div>
                </div>
            </div>

            <div class="dashboard-card">
                <div class="card-icon"><i class="fas fa-users"></i></div>
                <div class="card-content">
                    <div class="card-title">${L('dash.onlineUsers')}</div>
                    <div class="card-value" id="dashboard-online-users">–</div>
                    <div class="card-detail" id="dashboard-user-connections">–</div>
                </div>
            </div>

            <div class="dashboard-card">
                <div class="card-icon"><i class="fas fa-database"></i></div>
                <div class="card-content">
                    <div class="card-title">${L('dash.dataTransfer')}</div>
                    <div class="card-value" id="total-data">–</div>
                </div>
            </div>
        </div>

        <section class="dashboard-section">
            <h4><i class="fas fa-microchip"></i> ${L('dash.runtime')}</h4>
            <dl class="kv">
                <dt>${L('dash.cpu')}</dt>
                <dd class="kv-value" id="system-cpu">–</dd>
                <dt>${L('dash.memory')}</dt>
                <dd class="kv-value"><span id="system-memory">–</span><span class="card-detail" id="system-memory-detail"></span></dd>
                <dt>${L('dash.network')}</dt>
                <dd class="kv-value" id="system-bandwidth">–</dd>
                <dt>${L('dash.activeConnections')}</dt>
                <dd class="kv-value" id="active-connections">–</dd>
                <dt>${L('dash.maxConcurrent')}</dt>
                <dd class="kv-value" id="max-connections">–</dd>
                <dt>${L('dash.totalConnections')}</dt>
                <dd class="kv-value" id="total-connections">–</dd>
                <dt>${L('dash.rejected')}</dt>
                <dd class="kv-value" id="rejected-connections">–</dd>
                <dt>${L('dash.onlineMountPoints')}</dt>
                <dd class="kv-value" id="total-mounts">–</dd>
                <dt>${L('dash.userConnections')}</dt>
                <dd class="kv-value" id="total-users">–</dd>
            </dl>
        </section>

        <section class="dashboard-section">
            <h4><i class="fas fa-list"></i> ${L('dash.mountDetails')}</h4>
            <div class="mounts-container" id="mounts-detail">
                <div class="loading-text">${L('common.loading')}</div>
            </div>
        </section>
    `;
}

// ============ 客户端分页（列表数据量大时避免整页拉长） ============
// 分页状态：key 为列表类型（users / mounts），value 为 { page, pageSize }
const paginationState = {};

// 计算分页页码序列（带省略号，最多展示 7 个页码节点）
function buildPageList(current, total) {
    if (total <= 7) {
        const list = [];
        for (let i = 1; i <= total; i++) list.push(i);
        return list;
    }
    const pages = [1];
    if (current > 3) pages.push('...');
    const start = Math.max(2, current - 1);
    const end = Math.min(total - 1, current + 1);
    for (let i = start; i <= end; i++) pages.push(i);
    if (current < total - 2) pages.push('...');
    pages.push(total);
    return pages;
}

// 生成分页条 HTML。参数：listKey 列表类型、page 当前页、pageSize 每页条数、total 总条数
function renderPaginationBar(listKey, page, pageSize, total) {
    const totalPages = Math.max(1, Math.ceil(total / pageSize));
    const L = (key) => escapeHtml(t(key));
    const infoText = `${L('common.total')} ${total} · ${L('common.page')} ${page}/${totalPages}`;
    const pageNodes = buildPageList(page, totalPages).map(p => {
        if (p === '...') return `<span class="pagination-ellipsis">…</span>`;
        const active = p === page ? ' active' : '';
        return `<button type="button" class="pagination-num${active}" data-goto="${p}" data-list="${listKey}">${p}</button>`;
    }).join('');

    return `
        <div class="pagination-bar" data-scope="${listKey}">
            <span class="pagination-info">${infoText}</span>
            <div class="pagination-controls">
                <button type="button" class="btn btn-sm pagination-prev" data-list="${listKey}" data-page="${page}" ${page <= 1 ? 'disabled' : ''}>${L('common.prev')}</button>
                <div class="pagination-page-nums">${pageNodes}</div>
                <button type="button" class="btn btn-sm pagination-next" data-list="${listKey}" data-page="${page}" ${page >= totalPages ? 'disabled' : ''}>${L('common.next')}</button>
                <span class="pagination-size">
                    ${L('common.perPage')}
                    <select class="pagination-size-select" data-list="${listKey}">
                        <option value="20" ${pageSize === 20 ? 'selected' : ''}>20</option>
                        <option value="50" ${pageSize === 50 ? 'selected' : ''}>50</option>
                        <option value="100" ${pageSize === 100 ? 'selected' : ''}>100</option>
                    </select>
                </span>
            </div>
        </div>
    `;
}

// 已绑定的分页容器选择器集合，避免重复绑定
const paginationBound = {};

// 绑定分页条事件：委托到分页容器上（翻页后内部分页条重建，委托不失效）
function bindPaginationEvents(listKey, renderFn) {
    const containerSel = listKey === 'users' ? '#users-pagination' : '#mounts-pagination';
    if (paginationBound[containerSel]) return;
    const container = document.querySelector(containerSel);
    if (!container) return;
    paginationBound[containerSel] = true;

    container.addEventListener('click', function (e) {
        const prevBtn = e.target.closest('.pagination-prev');
        const nextBtn = e.target.closest('.pagination-next');
        const numBtn = e.target.closest('.pagination-num');
        if (prevBtn) {
            if (prevBtn.disabled) return;
            paginationState[listKey].page -= 1;
            renderFn();
        } else if (nextBtn) {
            if (nextBtn.disabled) return;
            paginationState[listKey].page += 1;
            renderFn();
        } else if (numBtn) {
            paginationState[listKey].page = parseInt(numBtn.getAttribute('data-goto'), 10);
            renderFn();
        }
    });

    container.addEventListener('change', function (e) {
        const sel = e.target.closest('.pagination-size-select');
        if (sel) {
            paginationState[listKey].pageSize = parseInt(sel.value, 10);
            paginationState[listKey].page = 1;
            renderFn();
        }
    });
}

// 通用分页渲染：slice 当前页数据后交给 rowRenderer 生成 <tr>，替换 tbody 并刷新分页条
function renderPaginatedRows(listKey, items, rowRenderer, tbodySelector, paginationTargetSelector, emptyText) {
    const state = paginationState[listKey] || { page: 1, pageSize: 20 };
    paginationState[listKey] = state;
    const total = items.length;
    const totalPages = Math.max(1, Math.ceil(total / state.pageSize));
    if (state.page > totalPages) state.page = totalPages;
    const start = (state.page - 1) * state.pageSize;

    const tbody = document.querySelector(tbodySelector);
    if (tbody) {
        if (total === 0 && emptyText) {
            tbody.innerHTML = `<tr><td colspan="10" class="empty-cell">${escapeHtml(emptyText)}</td></tr>`;
        } else {
            tbody.innerHTML = items.slice(start, start + state.pageSize).map(rowRenderer).join('');
        }
    }

    const pagTarget = document.querySelector(paginationTargetSelector);
    if (pagTarget) {
        pagTarget.innerHTML = total === 0 ? '' : renderPaginationBar(listKey, state.page, state.pageSize, total);
    }
}

// ============ 列表搜索过滤（纯前端，数据已全量缓存，无需后端/数据库） ============
// 搜索关键字状态：key 为列表类型，value 为关键字字符串
const searchState = {};

// 根据关键字过滤列表：前缀匹配排最前，包含匹配居中，其余剔除。
// 返回过滤后的新数组（不改变原缓存），空关键字返回原数组。
function filterByKeyword(list, keyword, keyFn) {
    const kw = (keyword || '').trim().toLowerCase();
    if (!kw) return list;
    const starts = [];
    const contains = [];
    for (const item of list) {
        const text = String(keyFn(item) || '').toLowerCase();
        if (!text) continue;
        if (text.startsWith(kw)) {
            starts.push(item);
        } else if (text.includes(kw)) {
            contains.push(item);
        }
    }
    return starts.concat(contains);
}

// 绑定搜索框事件：输入时 debounce 过滤 + 重置到第 1 页 + 重渲染
function bindSearchEvents(listKey, renderFn) {
    const input = document.querySelector(`#${listKey}-search-input`);
    const clearBtn = document.querySelector(`#${listKey}-search-clear`);
    if (!input) return;

    let timer = null;
    input.addEventListener('input', function () {
        clearTimeout(timer);
        timer = setTimeout(() => {
            searchState[listKey] = input.value;
            // 搜索变化后重置到第一页
            if (paginationState[listKey]) paginationState[listKey].page = 1;
            if (clearBtn) clearBtn.classList.toggle('hidden', !input.value);
            renderFn();
        }, 150);
    });

    if (clearBtn) {
        clearBtn.addEventListener('click', function () {
            input.value = '';
            searchState[listKey] = '';
            if (paginationState[listKey]) paginationState[listKey].page = 1;
            clearBtn.classList.add('hidden');
            renderFn();
        });
    }
}

// 渲染用户列表当前页（首屏及翻页复用）
function renderUsersPage() {
    const filtered = filterByKeyword(window._usersCache || [], searchState['users'], u => u.username);
    renderPaginatedRows('users', filtered, userRowRenderer, '#users-tbody', '#users-pagination', t('monitor.noData'));
    bindPaginationEvents('users', renderUsersPage);
}

// 渲染挂载点列表当前页（首屏及翻页复用）
function renderMountsPage() {
    const filtered = filterByKeyword(window._mountsCache || [], searchState['mounts'], m => m.mount);
    renderPaginatedRows('mounts', filtered, mountRowRenderer, '#mounts-tbody', '#mounts-pagination', t('monitor.noData'));
    bindPaginationEvents('mounts', renderMountsPage);
}

// user
function getUsersContent(users) {
    // 缓存原始数据，供翻页时重渲染
    window._usersCache = users || [];
    const L = (key) => escapeHtml(t(key));

    return `
        <div class="page-header">
            <h3>${L('users.title')}</h3>
            <button onclick="showAddUserForm()" class="btn btn-primary">${L('users.add')}</button>
        </div>
        <div class="list-toolbar">
            <div class="list-search">
                <i class="fas fa-search search-icon" aria-hidden="true"></i>
                <input type="text" id="users-search-input" placeholder="${L('common.searchPlaceholder')}" autocomplete="off" maxlength="50">
                <button type="button" class="search-clear hidden" id="users-search-clear" aria-label="${L('common.searchClear')}">
                    <i class="fas fa-times" aria-hidden="true"></i>
                </button>
            </div>
        </div>
        <div class="table-container">
            <div class="table-scroll">
                <table class="data-table">
                    <thead>
                        <tr>
                            <th>${L('users.col.username')}</th>
                            <th>${L('users.col.status')}</th>
                            <th>${L('users.col.connections')}</th>
                            <th>${L('users.col.connectTime')}</th>
                            <th>${L('users.col.actions')}</th>
                        </tr>
                    </thead>
                    <tbody id="users-tbody"></tbody>
                </table>
            </div>
        </div>
        <div id="users-pagination" class="pagination-wrap"></div>
    `;
}

// 单行用户渲染（供分页切片后调用）
function userRowRenderer(user) {
    const onlineHtml = `<span class="status-online">● ${escapeHtml(t('users.online'))}</span>`;
    const offlineHtml = `<span class="status-offline">○ ${escapeHtml(t('users.offline'))}</span>`;
    // 在线状态优先用 socket 实时推送的 window.onlineUsers（断线重连后能及时更新）。
    // 一旦收到过推送（window._usersSynced），就完全信任实时数据，不再回退静态 online 快照；
    // 否则（首次加载、socket 尚未推送）回退到 /api/users 的 online 字段。
    const isOnline = window._usersSynced
        ? (window.onlineUsers && (user.username in window.onlineUsers))
        : (user.online === true || (window.onlineUsers && (user.username in window.onlineUsers)));
    const statusHtml = isOnline ? onlineHtml : offlineHtml;
    const L = (key) => escapeHtml(t(key));

    // 从 window.onlineUsers 提取该用户的连接明细（含 mount_name + ip_address）
    const conns = (window.onlineUsers && window.onlineUsers[user.username]) || [];
    const isZh = getLang() === 'zh';
    const sep = isZh ? '、' : ', ';

    // 连接数：优先用实时数据（该用户在 window.onlineUsers 里的连接条数），否则回退静态快照
    const liveConnCount = conns.length;
    const connCount = liveConnCount > 0 ? liveConnCount : (user.connection_count || 0);

    // 连接数悬停：显示该用户连接的挂载点列表（去重），带前缀"使用挂载点中："
    const mountList = Array.from(new Set(conns.map(c => c.mount_name).filter(Boolean)));
    const connTitle = mountList.length > 0
        ? ` title="${escapeHtml((isZh ? '使用挂载点中：' : 'Using mount points: ') + mountList.join(sep))}"`
        : '';

    // 连接时间悬停：显示每条连接的 IP（去重），带前缀"连接IP："
    const ipList = Array.from(new Set(conns.map(c => c.ip_address).filter(Boolean)));
    const ipTitle = ipList.length > 0
        ? ` title="${escapeHtml((isZh ? '连接IP：' : 'Connection IPs: ') + ipList.join(sep))}"`
        : '';

    return `
        <tr class="user-row" data-username="${escapeHtml(user.username)}">
            <td>${escapeHtml(user.username)}</td>
            <td class="user-status">${statusHtml}</td>
            <td><span${connTitle} style="cursor:default">${escapeHtml(connCount)}</span></td>
            <td><span${ipTitle} style="cursor:default">${escapeHtml(user.connect_time || '-')}</span></td>
            <td>
                <button class="btn btn-primary btn-sm edit-user-btn" data-username="${escapeHtml(user.username)}">${L('common.edit')}</button>
                <button class="btn btn-danger btn-sm delete-user-btn" data-username="${escapeHtml(user.username)}">${L('common.delete')}</button>
            </td>
        </tr>
    `;
}

// 挂载点管理内容
function getMountsContent(mounts) {
    window._mountsCache = mounts || [];
    const L = (key) => escapeHtml(t(key));

    return `
        <div class="page-header">
            <h3>${L('mounts.title')}</h3>
            <button onclick="showAddMountForm()" class="btn btn-primary">${L('mounts.add')}</button>
        </div>
        <div class="list-toolbar">
            <div class="list-search">
                <i class="fas fa-search search-icon" aria-hidden="true"></i>
                <input type="text" id="mounts-search-input" placeholder="${L('common.searchPlaceholder')}" autocomplete="off" maxlength="50">
                <button type="button" class="search-clear hidden" id="mounts-search-clear" aria-label="${L('common.searchClear')}">
                    <i class="fas fa-times" aria-hidden="true"></i>
                </button>
            </div>
        </div>
        <div class="table-container">
            <div class="table-scroll">
                <table class="data-table">
                    <thead>
                        <tr>
                            <th>${L('mounts.col.mount')}</th>
                            <th>${L('mounts.col.status')}</th>
                            <th>${L('mounts.col.connections')}</th>
                            <th>${L('mounts.col.owner')}</th>
                            <th>${L('mounts.col.description')}</th>
                            <th>${L('mounts.col.actions')}</th>
                        </tr>
                    </thead>
                    <tbody id="mounts-tbody"></tbody>
                </table>
            </div>
        </div>
        <div id="mounts-pagination" class="pagination-wrap"></div>
    `;
}

// 单行挂载点渲染（供分页切片后调用）
function mountRowRenderer(mount) {
    const onlineHtml = `<span class="status-online">● ${escapeHtml(t('users.online'))}</span>`;
    const offlineHtml = `<span class="status-offline">○ ${escapeHtml(t('users.offline'))}</span>`;
    // 在线状态优先用 socket 实时推送的 window.onlineMounts（数据源断线重连后能及时更新）。
    // 一旦收到过推送（window._mountsSynced），就完全信任实时数据，不再回退静态 active 快照。
    const isOnline = window._mountsSynced
        ? (window.onlineMounts && (mount.mount in window.onlineMounts))
        : (mount.active === true || (window.onlineMounts && (mount.mount in window.onlineMounts)));
    const statusHtml = isOnline ? onlineHtml : offlineHtml;
    const L = (key) => escapeHtml(t(key));

    // 连接数：优先用实时数据（统计 window.onlineUsers 里连接该挂载点的连接数，与悬停名单同口径），
    // 否则回退到 /api/mounts 的静态 connections 快照
    let liveConnCount = 0;
    if (window.onlineUsers) {
        Object.values(window.onlineUsers).forEach(connList => {
            (Array.isArray(connList) ? connList : []).forEach(c => {
                if (c && c.mount_name === mount.mount) liveConnCount++;
            });
        });
    }
    const connCount = liveConnCount > 0 ? liveConnCount : (mount.connections || 0);

    // 连接数悬停：显示连接该挂载点的用户名（去重），带前缀"已连接用户："
    const connUsers = [];
    if (window.onlineUsers) {
        Object.values(window.onlineUsers).forEach(connList => {
            (Array.isArray(connList) ? connList : []).forEach(c => {
                if (c && c.mount_name === mount.mount && c.username && !connUsers.includes(c.username)) {
                    connUsers.push(c.username);
                }
            });
        });
    }
    const connTitle = connUsers.length > 0
        ? ` title="${escapeHtml((getLang() === 'zh' ? '已连接用户：' : 'Connected users: ') + connUsers.join(getLang() === 'zh' ? '、' : ', '))}"`
        : '';

    return `
        <tr class="mount-row" data-mount="${escapeHtml(mount.mount)}">
            <td>${escapeHtml(mount.mount)}</td>
            <td class="mount-status">${statusHtml}</td>
            <td><span${connTitle} style="cursor:default">${escapeHtml(connCount)}</span></td>
            <td>${escapeHtml(mount.username || t('common.unspecified'))}</td>
            <td>${escapeHtml(mount.description || '-')}</td>
            <td>
                <button onclick="editMount('${escapeHtml(mount.mount)}')" class="btn btn-primary btn-sm">${L('common.edit')}</button>
                <button onclick="deleteMount('${escapeHtml(mount.mount)}')" class="btn btn-danger btn-sm">${L('common.delete')}</button>
            </td>
        </tr>
    `;
}

// RTCM监控内容
function getMonitorContent() {
    const L = (key) => escapeHtml(t(key));
    return `
        <div class="page-header">
            <h3><i class="fas fa-satellite-dish"></i> ${L('monitor.title')}</h3>
            <p class="page-subtitle">${L('monitor.subtitle')}</p>
        </div>
        
        <div class="monitor-dashboard">
            <!-- 主要内容区域 -->
            <div class="monitor-grid">
                <!-- STR数据表 - 全宽 -->
                <div class="monitor-card full-width">
                    <div class="card-header">
                        <h4><i class="fas fa-table"></i> ${L('monitor.strTable')}</h4>
                    </div>
                    <div class="card-content" id="str-data">
                        <p class="loading-text"><i class="fas fa-spinner fa-spin"></i> ${L('monitor.strLoading')}</p>
                    </div>
                </div>

                <!-- 基准站信息 - 全宽 -->
                <div class="monitor-card full-width">
                    <div class="card-header">
                        <h4><i class="fas fa-broadcast-tower"></i> ${L('monitor.stationInfo')}</h4>
                        <div class="card-status" id="station-status">
                            <span class="status-dot waiting"></span>
                            <span>${L('monitor.waitSelect')}</span>
                        </div>
                    </div>
                    <div class="card-content">
                        <div id="station-info" class="station-info-container">
                            <div class="empty-state">
                                <i class="fas fa-mouse-pointer"></i>
                                <p>${L('monitor.selectHint')}</p>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- 基准站位置 - 全宽 -->
                <div class="monitor-card full-width">
                    <div class="card-header">
                        <h4><i class="fas fa-map-marker-alt"></i> ${L('monitor.stationLocation')}</h4>
                    </div>
                    <div class="card-content map-content">
                        <div id="map-container" class="map-container">
                            <div id="map" class="map-display"></div>
                            <div class="map-overlay" id="map-loading">
                                <i class="fas fa-map"></i>
                                <p>${L('monitor.waitLocation')}</p>
                            </div>
                            <div id="map-switch" class="map-switch-floating">
                                <button id="amap-btn" class="btn btn-sm btn-primary">${L('monitor.mapAmap')}</button>
                                <button id="osm-btn" class="btn btn-sm btn-secondary">${L('monitor.mapOsm')}</button>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- 卫星数据可视化 - 全宽 -->
                <div class="monitor-card full-width">
                    <div class="card-header">
                        <h4><i class="fas fa-satellite"></i> ${L('monitor.satViz')}</h4>
                        <div class="card-status" id="satellite-status">
                            <span class="status-dot waiting"></span>
                            <span>${L('monitor.waitData')}</span>
                        </div>
                    </div>
                    <div class="card-content">
                        <div id="satellite-container" class="satellite-container">
                            <div class="empty-state">
                                <i class="fas fa-satellite-dish"></i>
                                <p>${L('monitor.waitSat')}</p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `;
}

// settings
function getSettingsContent() {
    const L = (key) => escapeHtml(t(key));
    const P = (key) => escapeHtml(t(key)); // 占位符同 t()
    return `
        <div class="page-header">
            <h3><i class="fas fa-sliders-h"></i> ${L('settings.title')}</h3>
        </div>
        <div class="settings-container">
            <section class="settings-section">
                <h4><i class="fas fa-shield-alt"></i> ${L('settings.security')}</h4>
                <div class="form-group">
                    <label for="admin-password">${L('settings.newPassword')}</label>
                    <input type="password" id="admin-password" class="form-control"
                           placeholder="${P('ph.newPassword')}" maxlength="100" autocomplete="new-password"
                           data-validate="alnum" data-min="6" data-max="100" data-label="${L('field.newPassword')}">
                </div>
                <div class="form-group">
                    <label for="confirm-password">${L('settings.confirmPassword')}</label>
                    <input type="password" id="confirm-password" class="form-control"
                           placeholder="${P('ph.repeatPassword')}" maxlength="100" autocomplete="new-password"
                           data-validate="alnum" data-min="6" data-max="100" data-label="${L('field.confirmPassword')}">
                </div>
                <div class="field-hint">${L('settings.passwordHint')}</div>
                <div class="form-actions" style="justify-content: flex-start; margin-top: 12px;">
                    <button onclick="changePassword()" class="btn btn-primary">${L('settings.changePassword')}</button>
                </div>
            </section>

            <section class="settings-section">
                <h4><i class="fas fa-power-off"></i> ${L('settings.systemControl')}</h4>
                <div class="field-hint" style="margin-bottom: 12px;">
                    ${L('settings.restartHint')}
                </div>
                <button onclick="restartProgram()" class="btn btn-warning">${L('settings.restart')}</button>
            </section>
        </div>
    `;
}

// Socket.IO

socket.on('log_message', function(data) {
    addLogLine(data.message, data.type);
});

// user
socket.on('online_users_update', function(data) {
    window.onlineUsers = data.users;
    window._usersSynced = true;
    updateOnlineStatus();
});

// mounts
socket.on('online_mounts_update', function(data) {
    window.onlineMounts = data.mounts;
    window._mountsSynced = true;
    updateOnlineStatus();
});

// STR
socket.on('str_data_update', function(data) {
    window.strData = data.str_data;
    updateMonitorData();
});

// system
socket.on('system_stats_update', function(data) {
    if (currentPage === 'dashboard') {
        updateSystemStats(data.stats);
    } else if (currentPage === 'monitor') {
        updateMonitorStatus(data.stats);
    }
});

// 调试RTCM 数据
socket.on('rtcm_realtime_data', function(data) {
    // console.log('[前端接收] 收到RTCM实时数据:', data);
    // console.log('[前端接收] 数据类型:', typeof data);
    // console.log('[前端接收] 数据键:', Object.keys(data || {}));
    
    // 调试信息：显示除MSM类型外的其他数据类型
    if (data && data.data_type && data.data_type !== 'msm_satellite') {
        // console.log('[调试] 收到非MSM数据:', {
        //     数据类型: data.data_type,
        //     挂载点: data.mount_name || data.mount,
        //     时间戳: data.timestamp,
        //     数据内容: data
        // });
    }
    
    // 特别关注天线和设备信息
    if (data && data.data_type && ['device_info', 'antenna_info', 'receiver_info'].includes(data.data_type)) {
        // console.log('[天线设备调试] 收到天线/设备信息:', {
        //     数据类型: data.data_type,
        //     挂载点: data.mount_name || data.mount,
        //     接收机: data.receiver,
        //     固件: data.firmware,
        //     天线: data.antenna,
        //     天线序列号: data.antenna_firmware || data.antenna_serial,
        //     完整数据: data
        // });
    }
    
    if (!data || !data.data_type) {
        // console.warn('收到无效的RTCM数据:', data);
        return;
    }

    // 收到推送就证明后端确实在解析这个挂载点（rtcm_realtime_data 只由 Web 解析
    // 的 push_callback 发出）。若与当前高亮不一致，立即校正，不必等 5 秒轮询。
    const _pushMount = data.mount_name || data.mount;
    if (_pushMount && _pushMount !== activeParsingMount) {
        activeParsingMount = _pushMount;
        currentSelectedMount = _pushMount;
        applyMountButtonHighlight();
    }
    
    try {
        switch (data.data_type) {
            case 'station_position':
                // 处理基准站位置信息
                if (data.latitude && data.longitude) {
                    // console.log(`收到位置信息: ${data.latitude}, ${data.longitude}`);
                    
                    
                    if (!currentMap && currentPage === 'monitor') {
                        initializeMap();
                    }
                    
                    handlePositionUpdate(data.latitude, data.longitude);
                    
                    
                    updateElement('station-latitude', data.latitude.toFixed(6));
                    updateElement('station-longitude', data.longitude.toFixed(6));
                }
                break;
                
            case 'station_info':
               
                // console.log('收到基准站信息:', data);
                displayStationInfo(data);
                break;
                
            case 'msm_satellite':
               
                // console.log('收到卫星信号数据:', data);
                if (data.gnss && data.sats && Array.isArray(data.sats)) {
                    // 确保卫星可视化容器已初始化（只在第一次初始化）
                    if (currentPage === 'monitor') {
                        const satelliteContainer = document.getElementById('satellite-container');
                        if (satelliteContainer && !satelliteContainer.querySelector('.constellation-container')) {
                            initializeSatelliteVisualization();
                        }
                        
                        
                        const rtcmSatellites = data.sats.map(sat => ({
                            name: sat.id || sat.prn || t('common.unknown'),
                            signalStrength: sat.snr || sat.signal_strength || 0,
                            frequency: sat.frequency || 0,
                            channel: sat.signal_type || t('common.unknown')
                        }));
                        
                        
                        let constellation = data.gnss.toUpperCase();
                        if (constellation === 'BDS' || constellation === 'BEIDOU') {
                            constellation = 'BDS';
                        } else if (constellation === 'GLONASS' || constellation === 'GLO') {
                            constellation = 'GLONASS';
                        } else if (constellation === 'GPS') {
                            constellation = 'GPS';
                        } else if (constellation === 'GALILEO') {
                            constellation = 'GALILEO';
                        } else if (constellation === 'QZSS') {
                            constellation = 'QZSS';
                        } else if (constellation === 'IRNSS') {
                            constellation = 'IRNSS';
                        } else if (constellation === 'NAVIC' || constellation === 'NAV') {
                            constellation = 'NAVIC';
                        }
                        
                        updateSatelliteVisualization(constellation, rtcmSatellites);
                    }
                }
                break;
                
            case 'geography':
                // （1005/1006）
                // console.log('[地理信息调试] 收到地理位置信息:', data);
    // console.log('[地理信息调试] 当前页面:', currentPage);
                
                // 只在monitor页面处理基准站信息显示
                if (currentPage !== 'monitor') {
                    // console.log('[地理信息调试] 不在monitor页面，跳过基准站信息显示');
                    break;
                }
                
                
                const stationInfoDiv = document.getElementById('station-info');
                // console.log('[地理信息调试] station-info元素:', stationInfoDiv);
    // console.log('[地理信息调试] station-info内容:', stationInfoDiv ? stationInfoDiv.innerHTML : 'station-info不存在');
    // console.log('[地理信息调试] 是否有empty-state:', stationInfoDiv ? stationInfoDiv.querySelector('.empty-state') : 'station-info不存在');
    // console.log('[地理信息调试] 是否有station-details:', stationInfoDiv ? stationInfoDiv.querySelector('.station-details') : 'station-info不存在');
                
                if (stationInfoDiv && (stationInfoDiv.querySelector('.empty-state') || !stationInfoDiv.querySelector('.station-details'))) {
                    // 如果还是空状态，先创建基础结构
                    // console.log('[地理信息调试] 检测到empty-state，创建基础结构');
                    const stationData = {
                        name: data.mount_name || data.mount || t('common.unknown'),
                        id: data.station_id || t('common.unknown'),
                        country: data.country || t('common.unknown'),
                        city: data.city || t('common.unknown'),
                        latitude: data.lat || 0,
                        longitude: data.lon || 0,
                        height: data.height || t('common.unknown'),
                        x: data.x || 0,
                        y: data.y || 0,
                        z: data.z || 0,
                        receiver: { name: t('common.unknown'), firmware: t('common.unknown') },
                        antenna: { name: t('common.unknown'), serial: t('common.unknown') }
                    };
                    // console.log('[地理信息调试] 准备显示基准站信息:', stationData);
                    displayStationInfo(stationData);
                } else {
                    // 如果结构已存在，直接更新数据
                    // console.log('[地理信息调试] 基础结构已存在，更新数据');
        // console.log('[地理信息调试] 完整数据内容:', data);
                    
                    
                    if (data.mount_name || data.mount) {
                        // console.log('[地理信息调试] 更新挂载点名称:', data.mount_name || data.mount);
                        updateElement('station-name', data.mount_name || data.mount);
                    }
                    
                    
                    if (data.station_id !== undefined) {
                        // console.log('[地理信息调试] 更新基准站ID:', data.station_id);
                        updateElement('station-id', data.station_id.toString());
                    }
                    
                    
                    if (data.lat !== undefined && data.lon !== undefined) {
                        // console.log('[地理信息调试] 更新经纬度:', data.lat, data.lon);
                        
                        // 存储当前挂载点名称
                        currentMountName = data.mount_name || data.mount || null;
                        
                        
                        if (!currentMap && currentPage === 'monitor') {
                            initializeMap();
                        }
                        
                        handlePositionUpdate(data.lat, data.lon, currentMountName);
                        updateElement('station-latitude', data.lat.toFixed(6));
                        updateElement('station-longitude', data.lon.toFixed(6));
                    }
                    
                   
                    if (data.height !== undefined) {
                        // console.log('[地理信息调试] 更新高程:', data.height);
                        updateElement('station-height', data.height.toFixed(3) + ' m');
                    }
                    
                    // ECEF  XYZ
                    if (data.x !== undefined && data.y !== undefined && data.z !== undefined) {
                        // console.log('[地理信息调试] 更新XYZ坐标:', data.x, data.y, data.z);
                        updateElement('station-xyz', `X: ${data.x.toFixed(3)}, Y: ${data.y.toFixed(3)}, Z: ${data.z.toFixed(3)}`);
                    }
                    
                    // country
                    if (data.country || data.country_name) {
                        // console.log('[地理信息调试] 更新国家:', data.country_name || data.country);
                        updateElement('station-country', data.country_name || t('common.unknown'));
                    }
                    
                    // city
                    if (data.city) {
                        // console.log('[地理信息调试] 更新城市:', data.city);
                        updateElement('station-city', data.city);
                    }
                }
                break;
                
            case 'device_info':
                // （1033）
                // console.log('收到设备信息:', data);
                if (data.receiver) {
                    updateElement('receiver-type', data.receiver);
                }
                if (data.firmware) {
                    updateElement('receiver-version', data.firmware);
                }
                if (data.antenna) {
                    updateElement('antenna-type', data.antenna);
                }
                if (data.antenna_firmware) {
                    updateElement('antenna-serial', data.antenna_firmware);
                }
                break;
                
            case 'antenna_info':
                // console.log('收到天线信息:', data);
                if (data.antenna_type) {
                    updateElement('antenna-type', data.antenna_type);
                }
                if (data.antenna_serial) {
                    updateElement('antenna-serial', data.antenna_serial);
                }
                break;
                
            case 'receiver_info':
                // console.log('收到接收机信息:', data);
                if (data.receiver_type) {
                    updateElement('receiver-type', data.receiver_type);
                }
                if (data.receiver_version) {
                    updateElement('receiver-version', data.receiver_version);
                }
                break;
                
            default:
                // console.log(`未处理的数据类型: ${data.data_type}`, data);
                break;
        }
    } catch (error) {
        // console.error('处理RTCM数据时发生错误:', error, data);
    }
});


function updateElement(id, value) {
    const element = document.getElementById(id);
    if (element) {
        element.textContent = value;
    }
}


function updateSystemStats(stats) {
    if (!stats) return;

    updateElement('dashboard-timestamp',
        t('dash.updated', { time: new Date().toLocaleTimeString() }));

    if (stats.uptime !== undefined) {
        updateElement('system-uptime', formatUptime(stats.uptime));
    }

    if (stats.cpu_percent !== undefined) {
        updateElement('system-cpu', `${Number(stats.cpu_percent).toFixed(1)}%`);
    }

    if (stats.memory) {
        const memUsed = (stats.memory.used / 1073741824).toFixed(1);
        const memTotal = (stats.memory.total / 1073741824).toFixed(1);
        updateElement('system-memory', `${Number(stats.memory.percent).toFixed(1)}%`);
        updateElement('system-memory-detail', ` · ${memUsed} / ${memTotal} GB`);
    }

    if (stats.network_bandwidth) {
        const sent = formatBytes(stats.network_bandwidth.sent_rate || 0);
        const recv = formatBytes(stats.network_bandwidth.recv_rate || 0);
        updateElement('system-bandwidth', `↑${sent}/s ↓${recv}/s`);
    }

    if (stats.connections) {
        const conn = stats.connections;
        updateElement('active-connections', conn.active || 0);
        updateElement('max-connections', conn.max_concurrent || 0);
        updateElement('total-connections', conn.total || 0);
        updateElement('rejected-connections', conn.rejected || 0);
    }

    // 计数口径以后端 counts 为准：mounts=在线挂载点，users=在线账号，
    // connections=在线连接。老版本没有 counts，才退回自己数数组长度。
    const counts = stats.counts || {};
    const mountList = stats.mounts || [];
    const userList = stats.users || [];
    const mountCount = counts.mounts !== undefined
        ? counts.mounts
        : (Array.isArray(mountList) ? mountList.length : Object.keys(mountList).length);
    const connCount = counts.connections !== undefined
        ? counts.connections
        : (Array.isArray(userList) ? userList.length : Object.keys(userList).length);
    const userCount = counts.users !== undefined
        ? counts.users
        : new Set((Array.isArray(userList) ? userList : Object.values(userList))
            .map(u => u && u.username).filter(Boolean)).size;

    updateElement('total-mounts', mountCount);
    updateElement('dashboard-active-mounts', mountCount);
    updateElement('total-users', connCount);
    updateElement('dashboard-online-users', userCount);
    updateElement('dashboard-user-connections',
        t(connCount === 1 ? 'dash.connectionOne' : 'dash.connections', { n: connCount }));

    if (stats.data_transfer) {
        updateElement('total-data', formatBytes(stats.data_transfer.total_bytes || 0));
    }

    if (mountList) {
        updateMountDetails(mountList, userList);
    }
}


function formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}


function requestSystemStats() {
    socket.emit('request_system_stats');
}

// （API）
async function fetchSystemStats() {
    try {
        const response = await fetch('/api/system/stats');
        if (response.ok) {
            const stats = await response.json();
            updateSystemStats(stats);
        } else {
            // console.error('获取系统统计数据失败:', response.status);
        }
    } catch (error) {
        // console.error('获取系统统计数据异常:', error);
    }
}


function updateMountDetails(mounts, users) {
    const container = document.getElementById('mounts-detail');
    if (!container) return;

    // 后端历史上有时给数组、有时给 {name: info}，两种都接住
    const list = Array.isArray(mounts) ? mounts : Object.values(mounts || {});

    // 按挂载点聚合在线用户名（后端 users 明细已含 username + mount_name）
    // 同一用户可能有多条连接（默认上限 3），用 Set 去重，悬停只显示一次
    const usersByMount = {};
    (Array.isArray(users) ? users : []).forEach(u => {
        const mn = u && u.mount_name;
        const uname = u && u.username;
        if (mn && uname) {
            (usersByMount[mn] = usersByMount[mn] || new Set()).add(uname);
        }
    });

    if (list.length === 0) {
        container.innerHTML = `<div class="empty-state"><i class="fas fa-satellite-dish"></i><p>${escapeHtml(t('dash.noMounts'))}</p></div>`;
        return;
    }

    const sep = (typeof getLang === 'function' && getLang() === 'zh') ? '、' : ', ';

    const mountsHtml = list.map(mount => {
        const mountName = mount.mount_name || mount.mount || t('common.unknown');
        const userCount = mount.user_count || 0;
        const dataCount = mount.data_count || 0;
        const bytes = mount.total_bytes || 0;
        const rate = mount.data_rate || 0;
        const uptimeStr = formatUptime(mount.uptime || 0);
        const status = mount.status || 'unknown';
        // 有用户在线时，人形图标高亮为绿色
        const hasUsers = userCount > 0;
        // 悬停提示：列出连接到该基准站的用户名（已去重）
        const usernames = Array.from(usersByMount[mountName] || []);
        // 用户名/挂载点已由后端白名单校验（仅字母数字下划线中横线），直接原字符展示；
        // title 属性仍做一次转义，作为未来放宽校验时的安全兜底（不影响当前显示）
        const titleAttr = usernames.length > 0
            ? ` title="${escapeHtml((sep === '、' ? '已连接用户：' : 'Connected users: ') + usernames.join(sep))}"`
            : '';

        return `
            <div class="mount-item">
                <div class="mount-name">${mountName}</div>
                <div class="mount-stats">
                    <span class="mount-user-count${hasUsers ? ' has-users' : ''}"${titleAttr}><i class="fas fa-user"></i> ${userCount}</span>
                    <span><i class="fas fa-box"></i> ${dataCount}</span>
                    <span><i class="fas fa-database"></i> ${formatBytes(bytes)}</span>
                    <span><i class="fas fa-tachometer-alt"></i> ${formatBytes(rate)}/s</span>
                    <span><i class="fas fa-clock"></i> ${uptimeStr}</span>
                    <span class="mount-status">${status}</span>
                </div>
            </div>
        `;
    }).join('');

    container.innerHTML = mountsHtml;
}


function formatUptime(seconds) {
    if (!seconds || seconds < 0) return '0s';
    
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);
    
    if (days > 0) {
        return `${days}d ${hours}h ${minutes}m`;
    } else if (hours > 0) {
        return `${hours}h ${minutes}m`;
    } else if (minutes > 0) {
        return `${minutes}m ${secs}s`;
    } else {
        return `${secs}s`;
    }
}

// 
function updateOnlineStatus() {
    // 收到上下线推送后，直接重渲染当前页整行：
    // 之前只改 .user-status/.mount-status 的"在线/离线"文字，连接数数字、
    // 悬停提示（用户名/挂载点/IP）都不会刷新——数据源断线重连后，状态列虽更新
    // 但连接数仍是旧值、悬停内容仍是旧名单，表现为"在线状态未及时同步"。
    // 重渲染整页让状态、连接数、悬停提示三者一致。
    if (currentPage === 'users') {
        renderUsersPage();
    } else if (currentPage === 'mounts') {
        renderMountsPage();
    }
    updateDashboardCounts();
}

//
function updateDashboardCounts() {
    // users
    const onlineUsersCount = window.onlineUsers ? Object.keys(window.onlineUsers).length : 0;
    const dashboardOnlineUsersElement = document.getElementById('dashboard-online-users');
    if (dashboardOnlineUsersElement) {
        dashboardOnlineUsersElement.textContent = onlineUsersCount;
    }
    
    // mounts
    const activeMountsCount = window.onlineMounts ? Object.keys(window.onlineMounts).length : 0;
    const dashboardActiveMountsElement = document.getElementById('dashboard-active-mounts');
    if (dashboardActiveMountsElement) {
        dashboardActiveMountsElement.textContent = activeMountsCount;
    }
}

// INFO Buttons
                setTimeout(() => {
                    addInfoButtonsToSTRItems();
                }, 200);


function updateMonitorData() {
    if (currentPage === 'monitor' && window.strData) {
        const strDataElement = document.getElementById('str-data');
        if (strDataElement) {
            if (Object.keys(window.strData).length === 0) {
                strDataElement.innerHTML = `<div class="empty-state"><i class="fas fa-table"></i><p>${escapeHtml(t('monitor.strEmpty'))}</p></div>`;
            } else {
                let strHtml = '';
                Object.entries(window.strData).forEach(([mountName, strContent]) => {
                    strHtml += `
                        <div class="str-row">
                            <button class="str-info-btn" data-mount="${escapeHtml(mountName)}">${escapeHtml(mountName)}</button>
                            <div class="str-content-wrapper">
                                <div class="str-content-inline">${escapeHtml(strContent) || escapeHtml(t('monitor.noData'))}</div>
                            </div>
                        </div>
                    `;

                });
                strDataElement.innerHTML = strHtml;
                
                addInfoButtonsToSTRItems();
            }
        }
    }
}


function refreshSTRData() {
    const strContainer = document.getElementById('str-data');
    if (strContainer) {
        strContainer.innerHTML = `<p class="loading-text"><i class="fas fa-spinner fa-spin"></i> ${escapeHtml(t('monitor.strRefreshing'))}</p>`;
    }
    
    
    if (socket && socket.connected) {
        socket.emit('request_str_data');
    }
}


function updateMonitorStatus(systemStatus) {
    
    const connectionStatus = document.getElementById('connection-status-monitor');
    if (connectionStatus) {
        connectionStatus.textContent = socket && socket.connected ? t('rt.connected') : t('rt.disconnected');
    }
    
    
    const runtime = document.getElementById('runtime-monitor');
    if (runtime && systemStatus && systemStatus.uptime) {
        runtime.textContent = formatUptime(systemStatus.uptime);
    }
    
    
    const dataFlow = document.getElementById('data-flow-monitor');
    if (dataFlow && systemStatus && systemStatus.total_bytes) {
        dataFlow.textContent = formatBytes(systemStatus.total_bytes);
    }
}


function updateStationStatus(hasData) {
    const stationStatus = document.getElementById('station-status');
    if (stationStatus) {
        const statusDot = stationStatus.querySelector('.status-dot');
        const statusText = stationStatus.querySelector('span:last-child');
        
        if (hasData) {
            statusDot.className = 'status-dot online';
            statusText.textContent = t('monitor.selected');
        } else {
            statusDot.className = 'status-dot waiting';
            statusText.textContent = t('monitor.waitSelect');
        }
    }
}


function updateSatelliteStatus(hasData) {
    const satelliteStatus = document.getElementById('satellite-status');
    if (satelliteStatus) {
        const statusDot = satelliteStatus.querySelector('.status-dot');
        const statusText = satelliteStatus.querySelector('span:last-child');
        
        if (hasData) {
            statusDot.className = 'status-dot online';
            statusText.textContent = t('monitor.receiving');
        } else {
            statusDot.className = 'status-dot waiting';
            statusText.textContent = t('monitor.waitData');
        }
    }
}


function validateAlphanumeric(input, fieldName) {
   
    const validPattern = /^[a-zA-Z0-9_-]+$/;
    // fieldName 传的是已翻译好的字段名（如 t('field.username')）
    const label = fieldName || '';
    
    if (!input || input.trim() === '') {
        return { valid: false, message: t('valid.required', { field: label }) };
    }
    
    if (!validPattern.test(input)) {
        return { valid: false, message: t('valid.charset', { field: label }) };
    }
    
    return { valid: true, message: '' };
}

// ===== 表单实时校验（方案①：输入即提示，红边框 + 内联错误文字）=====
// 约定：需要实时校验的输入框带 data-validate="alnum" 属性，可选 data-min / data-max
// 以及 data-optional="1"（编辑表单里允许留空，留空时不报错）
function liveValidateField(input) {
    if (!input || !input.getAttribute('data-validate')) return { valid: true, message: '' };

    const value = input.value;
    const optional = input.getAttribute('data-optional') === '1';
    const label = input.getAttribute('data-label') || '';
    const min = parseInt(input.getAttribute('data-min'), 10);
    const max = parseInt(input.getAttribute('data-max'), 10);

    // 可选字段留空 = 通过（不校验）
    if (optional && value.trim() === '') {
        return { valid: true, message: '' };
    }

    const base = validateAlphanumeric(value, label);
    if (!base.valid) return base;

    // 长度校验（有 data-min/data-max 时）
    if (!isNaN(min) && !isNaN(max) && (value.length < min || value.length > max)) {
        return { valid: false, message: (label === t('field.username'))
            ? t('valid.userNameLen')
            : (label === t('field.mountName') ? t('valid.mountNameLen') : t('valid.passwordLen')) };
    }

    return { valid: true, message: '' };
}

// 更新单个输入框的实时错误提示（在 .form-group 内插入/更新 .field-error）
function setFieldError(input, message) {
    if (!input) return;
    const group = input.closest('.form-group');
    if (!group) return;

    // 移除旧的错误提示
    const old = group.querySelector('.field-error');
    if (old) old.remove();

    if (message) {
        input.classList.add('input-invalid');
        const small = document.createElement('small');
        small.className = 'field-error';
        small.textContent = message;
        input.insertAdjacentElement('afterend', small);
    } else {
        input.classList.remove('input-invalid');
    }
}

// 全局 input 事件委托：实时校验所有带 data-validate 的输入框
function initLiveValidation() {
    document.addEventListener('input', function (e) {
        const el = e.target;
        if (el && el.getAttribute && el.getAttribute('data-validate')) {
            const res = liveValidateField(el);
            setFieldError(el, res.valid ? '' : res.message);
        }
    });
}

// Add log line
// Debounced scroll function
let scrollTimeout = null;
function debouncedScroll(container) {
    if (scrollTimeout) {
        clearTimeout(scrollTimeout);
    }
    scrollTimeout = setTimeout(() => {
        container.scrollTop = container.scrollHeight;
    }, 10);
}

function addLogLine(message, type = 'info') {
    const logContainer = document.getElementById('log-terminal');
    if (logContainer) {
        // Use requestAnimationFrame to ensure DOM updates at appropriate time
        requestAnimationFrame(() => {
            const logEntry = document.createElement('div');
            logEntry.className = `log-line ${type}`;
            logEntry.textContent = `[${type.toUpperCase()}] ${message}`;
            
            // Disable animation to avoid flickering
            logEntry.style.animation = 'none';
            logEntry.style.transform = 'translateZ(0)'; // Enable hardware acceleration
            logEntry.style.willChange = 'auto';
            
            // Add directly to container, avoid extra overhead of document fragments
            logContainer.appendChild(logEntry);
            
            // Use debounced scroll
            debouncedScroll(logContainer);
            
            // Limit log entries, batch delete to reduce reflow
            const logEntries = logContainer.children;
            if (logEntries.length > 100) {
                // Delete first 10 entries to reduce frequent deletions
                requestAnimationFrame(() => {
                    for (let i = 0; i < 10 && logContainer.firstChild; i++) {
                        logContainer.removeChild(logContainer.firstChild);
                    }
                });
            }
        });
    }
}

// Initialization after page load completion
document.addEventListener('DOMContentLoaded', function() {
    // 图标字体走 CDN：内网/离线环境拿不到时，<i class="fas"> 会渲染成空方块（tofu），
    // 比没有图标更难看。这里检测一次，加载失败就给 <html> 加 no-icons，由 CSS 隐藏。
    try {
        if (document.fonts && !document.fonts.check('900 16px "Font Awesome 5 Free"')) {
            setTimeout(function() {
                if (!document.fonts.check('900 16px "Font Awesome 5 Free"')) {
                    document.documentElement.classList.add('no-icons');
                }
            }, 2500);
        }
    } catch (e) {
        // 老浏览器不支持 Font Loading API，忽略
    }

    // 表单实时校验：全局 input 委托（弹窗动态插入的输入框也能生效）
    initLiveValidation();

    // Initialize page
    // 登录页在认证成功后会带着 ?page=users|mounts|settings 跳回（见 /login 的 redirect 处理），
    // 这里必须消费这个参数，否则"点管理页被拦去登录"之后永远落在 dashboard
    const initialPage = new URLSearchParams(window.location.search).get('page');
    const allowedPages = ['dashboard', 'users', 'mounts', 'monitor', 'settings'];
    navigateTo(allowedPages.includes(initialPage) ? initialPage : 'dashboard');
    
    // Load frequency mapping table
    loadFrequencyMap();
    

    
    // Load application information
    loadAppInfo();
    
    // Navigation event listeners
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', function(e) {
            e.preventDefault();
            const page = this.getAttribute('data-page');
            if (page) {
                navigateTo(page);
            }
        });
    });

    // 用户列表编辑/删除按钮事件委托（列表分页后 tbody 会重建，故用全局委托）
    document.addEventListener('click', function (e) {
        const editBtn = e.target.closest('.edit-user-btn');
        if (editBtn) {
            const username = editBtn.getAttribute('data-username');
            if (username) editUser(username);
            return;
        }
        const delBtn = e.target.closest('.delete-user-btn');
        if (delBtn) {
            const username = delBtn.getAttribute('data-username');
            if (username) deleteUser(username);
        }
    });
});

// User management functions
function showAddUserForm() {
    const formHtml = `
        <div class="modal-overlay" id="userModal">
            <div class="modal-content">
                <h4>${escapeHtml(t('users.addTitle'))}</h4>
                <div class="form-group">
                    <label>${escapeHtml(t('users.col.username'))}</label>
                    <input class="form-control" type="text" id="newUsername" placeholder="${escapeHtml(t('ph.username'))}" maxlength="50"
                           data-validate="alnum" data-min="3" data-max="50" data-label="${escapeHtml(t('field.username'))}">
                </div>
                <div class="form-group">
                    <label>${escapeHtml(t('login.password'))}</label>
                    <input class="form-control" type="password" id="newPassword" placeholder="${escapeHtml(t('ph.password'))}" maxlength="100"
                           data-validate="alnum" data-min="6" data-max="100" data-label="${escapeHtml(t('field.password'))}">
                </div>
                <div class="form-actions">
                    <button class="btn btn-secondary" onclick="closeModal('userModal')">${escapeHtml(t('common.cancel'))}</button>
                    <button class="btn btn-success" onclick="submitAddUser()">${escapeHtml(t('common.add'))}</button>
                </div>
            </div>
        </div>
    `;
    document.body.insertAdjacentHTML('beforeend', formHtml);
}

function submitAddUser() {
    const username = document.getElementById('newUsername').value.trim();
    const password = document.getElementById('newPassword').value;
    
    // username
    const usernameValidation = validateAlphanumeric(username, t('field.username'));
    if (!usernameValidation.valid) {
        showAlert(usernameValidation.message, 'error');
        return;
    }
    
    if (username.length < 3 || username.length > 50) {
        showAlert(t('valid.userNameLen'), 'error');
        return;
    }
    
    // password
    const passwordValidation = validateAlphanumeric(password, t('field.password'));
    if (!passwordValidation.valid) {
        showAlert(passwordValidation.message, 'error');
        return;
    }
    
    if (password.length < 6 || password.length > 100) {
        showAlert(t('valid.passwordLen'), 'error');
        return;
    }
    
    addUser(username, password);
    closeModal('userModal');
}

async function addUser(username, password) {
    try {
        const response = await fetch('/api/users', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ username, password })
        });
        
        const result = await handleApiResponse(response);
        loadPageContent('users'); // Refresh user list
    } catch (error) {
        if (!error.isAuthError) {
            showAlert(t('msg.userAddFailed', { error: error.message }), 'error');
        }
    }
}

function editUser(username) {
    const isAdmin = username === 'admin';
    const formHtml = `
        <div class="modal-overlay" id="editUserModal">
            <div class="modal-content">
                <h4>${escapeHtml(t('users.editTitle'))} - ${escapeHtml(username)}</h4>
                ${!isAdmin ? `
                <div class="form-group">
                    <label>${escapeHtml(t('users.col.username'))}</label>
                    <input class="form-control" type="text" id="editUsername" value="${escapeHtml(username)}" maxlength="50"
                           data-validate="alnum" data-min="3" data-max="50" data-label="${escapeHtml(t('field.username'))}">
                </div>
                ` : `
                <div class="form-group">
                    <label>${escapeHtml(t('users.col.username'))}</label>
                    <input class="form-control" type="text" value="${escapeHtml(username)}" disabled>
                    <small>${escapeHtml(t('users.adminLocked'))}</small>
                </div>
                `}
                <div class="form-group">
                    <label>${escapeHtml(t('users.newPasswordOptional'))}</label>
                    <input class="form-control" type="password" id="editPassword" placeholder="${escapeHtml(t('ph.keepPassword'))}" maxlength="100"
                           data-validate="alnum" data-min="6" data-max="100" data-label="${escapeHtml(t('field.password'))}" data-optional="1">
                </div>
                <div class="form-actions">
                    <button class="btn btn-secondary" onclick="closeModal('editUserModal')">${escapeHtml(t('common.cancel'))}</button>
                    <button class="btn btn-success" onclick="submitEditUser('${username}')">${escapeHtml(t('common.save'))}</button>
                </div>
            </div>
        </div>
    `;
    document.body.insertAdjacentHTML('beforeend', formHtml);
}

function submitEditUser(originalUsername) {
    const newUsername = document.getElementById('editUsername')?.value.trim();
    const newPassword = document.getElementById('editPassword').value.trim();
    
    const updateData = {};
    
    // If password is entered, validate and add to update data
    if (newPassword) {
        const passwordValidation = validateAlphanumeric(newPassword, t('field.password'));
        if (!passwordValidation.valid) {
            showAlert(passwordValidation.message, 'error');
            return;
        }
        if (newPassword.length < 6 || newPassword.length > 100) {
            showAlert(t('valid.passwordLen'), 'error');
            return;
        }
        updateData.password = newPassword;
    }
    
    // If not admin and username has changed
    if (originalUsername !== 'admin' && newUsername && newUsername !== originalUsername) {
        const usernameValidation = validateAlphanumeric(newUsername, t('field.username'));
        if (!usernameValidation.valid) {
            showAlert(usernameValidation.message, 'error');
            return;
        }
        if (newUsername.length < 3 || newUsername.length > 50) {
            showAlert(t('valid.userNameLen'), 'error');
            return;
        }
        updateData.username = newUsername;
    }
    
    // Check if there are any updates
    if (Object.keys(updateData).length === 0) {
        showAlert(t('valid.noChanges'), 'warning');
        return;
    }
    
    updateUser(originalUsername, updateData);
    closeModal('editUserModal');
}

async function updateUser(username, data) {
    try {
        const response = await fetch(`/api/users/${username}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(data)
        });                
        const result = await handleApiResponse(response);
        showAlert(localizeServerMessage(result.message), 'success');
        loadPageContent('users'); // Refresh user list
    } catch (error) {
        if (!error.isAuthError) {
            showAlert(t('msg.userUpdateFailed', { error: error.message }), 'error');
        }
    }
}

function deleteUser(username) {
    // console.log('deleteUser called with username:', username);
    showConfirmDialog(
        t('users.confirmDeleteTitle'),
        t('users.confirmDelete', { name: username }),
        () => {
            // console.log('User confirmed deletion');
            removeUser(username);
        },
        () => {
            // console.log('User cancelled deletion');
        }
    );
}

async function removeUser(username) {
    // console.log('removeUser called with username:', username);
    try {
        // console.log('Sending DELETE request to:', `/api/users/${username}`);
        const response = await fetch(`/api/users/${username}`, {
            method: 'DELETE'
        });
        
        // console.log('Response status:', response.status);
        const result = await handleApiResponse(response);
        // console.log('API response result:', result);
        // Refresh list directly after successful deletion, no success popup
        loadPageContent('users'); // Refresh user list
    } catch (error) {
        // console.error('Error in removeUser:', error);
        if (!error.isAuthError) {
            showAlert(t('msg.userDeleteFailed', { error: error.message }), 'error');
        }
    }
}

// Mount point management functions
async function showAddMountForm() {
    // Get user list for dropdown selection
    let usersOptions = `<option value="">${escapeHtml(t('mounts.noBinding'))}</option>`;
    try {
        const response = await fetch('/api/users');
        if (response.ok) {
            const users = await response.json();
            users.forEach(user => {
                usersOptions += `<option value="${user.id}">${escapeHtml(user.username)}</option>`;
            });
        }
    } catch (error) {
        // console.error('Failed to get user list:', error);
    }
    
    const formHtml = `
        <div class="modal-overlay" id="mountModal">
            <div class="modal-content">
                <h4>${escapeHtml(t('mounts.addTitle'))}</h4>
                <div class="form-group">
                    <label>${escapeHtml(t('mounts.name'))}</label>
                    <input class="form-control" type="text" id="newMountName" placeholder="${escapeHtml(t('ph.mountName'))}" maxlength="50"
                           data-validate="alnum" data-min="3" data-max="50" data-label="${escapeHtml(t('field.mountName'))}">
                </div>
                <div class="form-group">
                    <label>${escapeHtml(t('mounts.password'))}</label>
                    <input class="form-control" type="password" id="newMountPassword" placeholder="${escapeHtml(t('ph.password'))}" maxlength="100"
                           data-validate="alnum" data-min="6" data-max="100" data-label="${escapeHtml(t('field.password'))}">
                </div>
                <div class="form-group">
                    <label>${escapeHtml(t('mounts.bindUser'))}</label>
                    <select id="newMountUser">
                        ${usersOptions}
                    </select>
                </div>
                <div class="form-actions">
                    <button class="btn btn-secondary" onclick="closeModal('mountModal')">${escapeHtml(t('common.cancel'))}</button>
                    <button class="btn btn-success" onclick="submitAddMount()">${escapeHtml(t('common.add'))}</button>
                </div>
            </div>
        </div>
    `;
    document.body.insertAdjacentHTML('beforeend', formHtml);
}

function submitAddMount() {
    const mountName = document.getElementById('newMountName').value.trim();
    const password = document.getElementById('newMountPassword').value;
    const userId = document.getElementById('newMountUser').value;
    
    // Validate mount point name
    const mountNameValidation = validateAlphanumeric(mountName, t('field.mountName'));
    if (!mountNameValidation.valid) {
        showAlert(mountNameValidation.message, 'error');
        return;
    }
    
    // Validate password
    const passwordValidation = validateAlphanumeric(password, t('field.password'));
    if (!passwordValidation.valid) {
        showAlert(passwordValidation.message, 'error');
        return;
    }
    
    if (mountName.length < 3 || mountName.length > 50) {
        showAlert(t('valid.mountNameLen'), 'error');
        return;
    }
    
    if (password.length < 6 || password.length > 100) {
        showAlert(t('valid.passwordLen'), 'error');
        return;
    }
    
    const mountData = { mount: mountName, password: password };
    if (userId) {
        mountData.user_id = parseInt(userId);
    }
    
    addMount(mountData);
    closeModal('mountModal');
}

async function addMount(mountData) {
    try {
        const response = await fetch('/api/mounts', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(mountData)
        });
        
        const result = await handleApiResponse(response);
        loadPageContent('mounts'); // Refresh mount point list
    } catch (error) {
        if (!error.isAuthError) {
            showAlert(t('msg.mountAddFailed', { error: error.message }), 'error');
        }
    }
}

async function editMount(mount) {
    let currentMountData = null;
    let currentUsername = '';
    
    try {
        // Get current mount point information
        const mountResponse = await fetch('/api/mounts');
        if (mountResponse.ok) {
            const mounts = await mountResponse.json();
            currentMountData = mounts.find(m => m.mount === mount);
            // /api/mounts 已经直接返回绑定用户名，不必再查一次用户表
            // （原来多打一次 /api/users，一旦这次请求失败 currentUsername 就是空，
            //  保存口令时会顺手把绑定清掉）
            if (currentMountData && currentMountData.username) {
                currentUsername = currentMountData.username;
            }
        }
    } catch (error) {
        // console.error('Failed to get data:', error);
    }
    
    const formHtml = `
        <div class="modal-overlay" id="editMountModal">
            <div class="modal-content">
                <h4>${escapeHtml(t('mounts.editTitle'))} - ${escapeHtml(mount)}</h4>
                <div class="form-group">
                    <label>${escapeHtml(t('mounts.name'))}</label>
                    <input class="form-control" type="text" id="editMountName" value="${escapeHtml(mount)}" data-original="${escapeHtml(mount)}" maxlength="50"
                           data-validate="alnum" data-min="3" data-max="50" data-label="${escapeHtml(t('field.mountName'))}">
                </div>
                <div class="form-group">
                    <label>${escapeHtml(t('mounts.newPassword'))}</label>
                    <input class="form-control" type="password" id="editMountPassword" placeholder="${escapeHtml(t('ph.keepPassword'))}" maxlength="100"
                           data-validate="alnum" data-min="6" data-max="100" data-label="${escapeHtml(t('field.password'))}" data-optional="1">
                </div>
                <div class="form-group">
                    <label>${escapeHtml(t('mounts.bindUser'))}</label>
                    <input class="form-control" type="text" id="editMountUser" value="${escapeHtml(currentUsername)}" data-original="${escapeHtml(currentUsername)}" placeholder="${escapeHtml(t('ph.bindUser'))}" maxlength="50">
                </div>
                <div class="form-actions">
                    <button class="btn btn-secondary" onclick="closeModal('editMountModal')">${escapeHtml(t('common.cancel'))}</button>
                    <button class="btn btn-success" onclick="submitEditMount('${escapeHtml(mount)}')">${escapeHtml(t('common.save'))}</button>
                </div>
            </div>
        </div>
    `;
    document.body.insertAdjacentHTML('beforeend', formHtml);
}

async function submitEditMount(originalMount) {
    const newMountName = document.getElementById('editMountName').value.trim();
    const newPassword = document.getElementById('editMountPassword').value.trim();
    const username = document.getElementById('editMountUser').value.trim();
    
    const updateData = {};
    
    // If password is entered, validate and add to update data
    if (newPassword) {
        const passwordValidation = validateAlphanumeric(newPassword, t('field.password'));
        if (!passwordValidation.valid) {
            showAlert(passwordValidation.message, 'error');
            return;
        }
        if (newPassword.length < 6 || newPassword.length > 100) {
            showAlert(t('valid.passwordLen'), 'error');
            return;
        }
        updateData.password = newPassword;
    }
    
    // If mount point name has changed and is not empty
    if (newMountName && newMountName !== originalMount) {
        const mountNameValidation = validateAlphanumeric(newMountName, t('field.mountName'));
        if (!mountNameValidation.valid) {
            showAlert(mountNameValidation.message, 'error');
            return;
        }
        if (newMountName.length < 3 || newMountName.length > 50) {
            showAlert(t('valid.mountNameLen'), 'error');
            return;
        }
        updateData.mount_name = newMountName;
    }
    
    // Handle username binding
    const userInput = document.getElementById('editMountUser');
    const originalUsername = userInput ? (userInput.dataset.original || '') : '';
    if (username && username !== originalUsername) {
        const usernameValidation = validateAlphanumeric(username, t('field.username'));
        if (!usernameValidation.valid) {
            showAlert(usernameValidation.message, 'error');
            return;
        }
    }
    // 只有绑定真的变了才提交：否则把"原值为空/读取失败"当成解绑写进库，
    // 用户明明只想改口令，绑定却被清掉了
    if (username !== originalUsername) {
        updateData.username = username;
    }
    
    // Check if there are any updates
    if (Object.keys(updateData).length === 0) {
        showAlert(t('valid.noChanges'), 'warning');
        return;
    }
    
    updateMount(originalMount, updateData);
    closeModal('editMountModal');
}

async function updateMount(mount, data) {
    try {
        const response = await fetch(`/api/mounts/${mount}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(data)
        });
        
        const result = await handleApiResponse(response);
        showAlert(localizeServerMessage(result.message), 'success');
        loadPageContent('mounts'); // Refresh mount point list
    } catch (error) {
        if (!error.isAuthError) {
            showAlert(t('msg.mountUpdateFailed', { error: error.message }), 'error');
        }
    }
}

function deleteMount(mount) {
    showConfirmDialog(
        t('mounts.confirmDeleteTitle'),
        t('mounts.confirmDelete', { name: mount }),
        () => {
            removeMount(mount);
        },
        () => {
            // User cancelled deletion
        }
    );
}

async function removeMount(mount) {
        try {
            const response = await fetch(`/api/mounts/${mount}`, {
                method: 'DELETE'
            });
            
            const result = await handleApiResponse(response);
            // Refresh list directly after successful deletion, no success popup
            loadPageContent('mounts'); // Refresh mount point list
        } catch (error) {
            if (!error.isAuthError) {
                showAlert(t('msg.mountDeleteFailed', { error: error.message }), 'error');
            }
        }
    }
    

    
    async function changePassword() {
        const newPassword = document.getElementById('admin-password').value;
        const confirmPassword = document.getElementById('confirm-password').value;
        
        if (!newPassword || !confirmPassword) {
            showAlert(t('valid.passwordRequired'), 'warning');
            return;
        }
        
        // Validate new password
        const passwordValidation = validateAlphanumeric(newPassword, t('field.newPassword'));
        if (!passwordValidation.valid) {
            showAlert(passwordValidation.message, 'error');
            return;
        }
        
        // Validate confirm password
        const confirmPasswordValidation = validateAlphanumeric(confirmPassword, t('field.confirmPassword'));
        if (!confirmPasswordValidation.valid) {
            showAlert(confirmPasswordValidation.message, 'error');
            return;
        }
        
        if (newPassword !== confirmPassword) {
            showAlert(t('valid.passwordMismatch'), 'error');
            return;
        }
        
        if (newPassword.length < 6) {
            showAlert(t('valid.passwordTooShort'), 'error');
            return;
        }
        
        try {
            const response = await fetch('/api/admin/password', {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ password: newPassword })
            });
            
            const result = await response.json();
            
            if (response.ok) {
                showAlert(t('msg.passwordChanged'), 'success');
                document.getElementById('admin-password').value = '';
                document.getElementById('confirm-password').value = '';
            } else {
                showAlert(t('msg.httpError', {
                    error: localizeServerMessage(result.error) || `HTTP ${response.status}`
                }), 'error');
            }
        } catch (error) {
            // console.error('Failed to change password:', error);
            showAlert(t('msg.passwordChangeFailed', { error: error.message }), 'error');
        }
    }
    
    async function restartProgram() {
        showConfirmDialog(
        t('settings.confirmRestartTitle'),
        t('settings.confirmRestart'),
        async function() {
            // Execute restart logic
            await performRestart();
        }
    );
}

async function performRestart() {
        
        try {
            const response = await fetch('/api/system/restart', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            });
            
            if (response.ok) {
                showAlert(t('settings.restartSent'), 'success');
                // Refresh page after 3 seconds
                setTimeout(() => {
                    window.location.reload();
                }, 3000);
            } else {
                const result = await response.json();
                showAlert(t('settings.restartFailed', {
                    error: localizeServerMessage(result.error) || t('err.unknown')
                }), 'error');
            }
        } catch (error) {
            // console.error('Failed to restart program:', error);
            showAlert(t('msg.restartInvokeFailed', { error: error.message }), 'error');
        }
    }


/* ==========================================================================
   打赏弹窗：支付宝 / 微信收款码
   --------------------------------------------------------------------------
   图片放在 static/img/ 下（Dockerfile 已整体拷贝 static 目录），不依赖外网 CDN。
   打开时先移除旧节点，切换语言后可直接重调本函数重建一份新语言的弹窗。
   ========================================================================== */
const DONATE_QR = [
    { key: 'alipay', img: '/static/img/donate-alipay.jpg', name: 'donate.alipay', hint: 'donate.scanAlipay', icon: 'fas fa-qrcode', cls: 'donate-alipay' },
    { key: 'wechat', img: '/static/img/donate-wechat.jpg', name: 'donate.wechat', hint: 'donate.scanWechat', icon: 'fas fa-comment-dots', cls: 'donate-wechat' }
];

function openDonateModal() {
    const modalId = 'donateModal';
    const existing = document.getElementById(modalId);
    if (existing) {
        existing.remove();
    }

    const cards = DONATE_QR.map(item => `
        <figure class="donate-card">
            <a href="${item.img}" target="_blank" rel="noopener" title="${escapeHtml(t(item.hint))}">
                <img src="${item.img}" alt="${escapeHtml(t(item.name))}" loading="lazy">
            </a>
            <figcaption>
                <span class="donate-name ${item.cls}"><i class="${item.icon}"></i> ${escapeHtml(t(item.name))}</span>
                <span class="donate-hint">${escapeHtml(t(item.hint))}</span>
            </figcaption>
        </figure>
    `).join('');

    const modalHtml = `
        <div class="modal-overlay" id="${modalId}" role="dialog" aria-modal="true">
            <div class="modal-content donate-modal">
                <button type="button" class="modal-close" onclick="closeModal('${modalId}')"
                        aria-label="${escapeHtml(t('common.close'))}">&times;</button>
                <h4 class="donate-title"><i class="fas fa-heart"></i> ${escapeHtml(t('donate.title'))}</h4>
                <p class="donate-subtitle">${escapeHtml(t('donate.subtitle'))}</p>
                <div class="donate-grid">${cards}</div>
                <p class="donate-thanks">${escapeHtml(t('donate.thanks'))}</p>
                <p class="donate-note">${escapeHtml(t('donate.note'))}</p>
                <div class="form-actions" style="justify-content: center;">
                    <button type="button" class="btn btn-secondary" onclick="closeModal('${modalId}')">${escapeHtml(t('donate.close'))}</button>
                </div>
            </div>
        </div>
    `;

    document.body.insertAdjacentHTML('beforeend', modalHtml);

    const modal = document.getElementById(modalId);
    if (modal) {
        modal.addEventListener('click', function (e) {
            if (e.target === modal) {
                closeModal(modalId);
            }
        });
    }
}

/* 语言切换：静态文案由 i18n.js 处理，这里负责重绘动态区域 */
window.addEventListener('i18n:changed', function () {
    // 延到下一个任务执行：i18n.js 自己也监听该事件刷新静态文案（data-i18n），
    // 若同步执行，随后的 applyI18n() 会把 #realtime-status 又重置成“连接中…”，
    // 覆盖掉这里算出来的真实连接状态。
    setTimeout(function () {
        try {
            // 已渲染的提示/确认框文案不会自动更新，直接关掉，避免中英混排
            document.querySelectorAll('#alertDialog, #confirmDialog, #userModal, #editUserModal, #mountModal, #editMountModal')
                .forEach(el => el.remove());

            const donateOpen = !!document.getElementById('donateModal');
            if (donateOpen) {
                openDonateModal();
            }

            updateConnectionStatus();

            // 当前页内容整体重建（内部所有文案都走 t()）
            if (currentPage) {
                loadPageContent(currentPage);
            }
        } catch (error) {
            console.warn('[i18n] re-render failed:', error);
        }
    }, 0);
});


function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.remove();
    }
}


function showAlert(message, type = 'info') {
    const modalId = 'alertDialog';
    
    
    const existingModal = document.getElementById(modalId);
    if (existingModal) {
        existingModal.remove();
    }
    
    const iconMap = {
        'info': 'ℹ️',
        'success': '✔️',
        'error': '✖️',
        'warning': '⚠️'
    };
    
    const colorMap = {
        'info': '#3498db',
        'success': '#27ae60',
        'error': '#e74c3c',
        'warning': '#f39c12'
    };
    
    const modalHtml = `
        <div class="modal-overlay" id="${modalId}">
            <div class="modal-content" style="max-width: 400px; text-align: center;">
                <div style="font-size: 2rem; margin-bottom: 1rem;">${iconMap[type] || iconMap['info']}</div>
                <p style="margin-bottom: 2rem; color: #666; line-height: 1.5; font-size: 1.1rem;">${escapeHtml(message)}</p>
                <div style="display: flex; justify-content: center;">
                    <button class="btn" style="background: ${colorMap[type] || colorMap['info']}; color: white; border: none;" onclick="closeModal('${modalId}')">${escapeHtml(t('common.ok'))}</button>
                </div>
            </div>
        </div>
    `;
    
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    
    // Click background to close
    document.getElementById(modalId).addEventListener('click', function(e) {
        if (e.target === this) {
            closeModal(modalId);
        }
    });
    
    // Close with ESC key
    const escHandler = function(e) {
        if (e.key === 'Escape') {
            closeModal(modalId);
            document.removeEventListener('keydown', escHandler);
        }
    };
    document.addEventListener('keydown', escHandler);
}

// Show confirmation dialog
function showConfirmDialog(title, message, onConfirm, onCancel) {
    const modalId = 'confirmDialog';
    
    // Remove existing confirmation dialog
    const existingModal = document.getElementById(modalId);
    if (existingModal) {
        existingModal.remove();
    }
    
    const modalHtml = `
        <div class="modal-overlay" id="${modalId}">
            <div class="modal-content" style="max-width: 400px;">
                <h4>${escapeHtml(title)}</h4>
                <p style="margin-bottom: 2rem; color: #666; line-height: 1.5;">${escapeHtml(message)}</p>
                <div style="display: flex; gap: 1rem; justify-content: flex-end;">
                    <button class="btn btn-secondary" onclick="cancelConfirm()">${escapeHtml(t('common.cancel'))}</button>
                    <button class="btn btn-primary" onclick="confirmAction()">${escapeHtml(t('common.confirm'))}</button>
                </div>
            </div>
        </div>
    `;
    
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    
    // Temporarily store callback functions
    window.tempConfirmCallback = onConfirm;
    window.tempCancelCallback = onCancel;
    
    
    document.getElementById(modalId).addEventListener('click', function(e) {
        if (e.target === this) {
            cancelConfirm();
        }
    });
}

// Confirm action
function confirmAction() {
    if (window.tempConfirmCallback) {
        window.tempConfirmCallback();
    }
    closeModal('confirmDialog');
    // Clean up temporary callbacks
    window.tempConfirmCallback = null;
    window.tempCancelCallback = null;
}

// Cancel action
function cancelConfirm() {
    if (window.tempCancelCallback) {
        window.tempCancelCallback();
    }
    closeModal('confirmDialog');
    // Clean up temporary callbacks
    window.tempConfirmCallback = null;
    window.tempCancelCallback = null;
}
    
    // Click modal background to close
    document.addEventListener('click', function(e) {
        if (e.target.classList.contains('modal-overlay')) {
            e.target.remove();
        }
    });
    
    // Close modal with ESC key
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            const modals = document.querySelectorAll('.modal-overlay');
            modals.forEach(modal => modal.remove());
        }
    });
    
    // Logout function
    function logout() {
        // Simplified logout process, execute logout operation directly
        showConfirmDialog(
            t('confirm.logoutTitle'),
            t('confirm.logout'),
            () => {
                fetch('/logout', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    }
                }).then(() => {
                    window.location.href = '/login';
                }).catch(error => {
                     // console.error('Logout failed:', error);
                     window.location.href = '/login';
                 });
             },
             () => {
                 // User cancelled logout
             }
         );
    }


async function loadAppInfo() {
    try {
        const response = await fetch('/api/app_info');
        if (response.ok) {
            const appInfo = await response.json();
            
            // Update footer information
            document.getElementById('app-name').textContent = appInfo.name;
            document.getElementById('app-version').textContent = `v${appInfo.version}`;
            document.getElementById('app-author').textContent = appInfo.author;
            
            const contactElement = document.getElementById('app-contact');
            contactElement.textContent = appInfo.contact;
            contactElement.href = `mailto:${appInfo.contact}`;
            
            const websiteElement = document.getElementById('app-website');
            websiteElement.textContent = appInfo.website.replace('https://', '').replace('http://', '');
            websiteElement.href = appInfo.website;
        }
    } catch (error) {
        // console.error('Failed to load application information:', error);
    }
}