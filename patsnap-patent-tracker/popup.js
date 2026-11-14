/**
 * 智慧芽专利浏览追踪助手 - Popup 交互逻辑
 * 支持：多项目隔离管理、开始/暂停追踪开关、相对时间显示、精准导出与清理
 */

document.addEventListener('DOMContentLoaded', () => {
  // 顶部开始/停止开关
  const btnToggleTracking = document.getElementById('btnToggleTracking');
  const statusBtnText = document.getElementById('statusBtnText');

  // 项目管理控件
  const projectSelect = document.getElementById('projectSelect');
  const btnNewProject = document.getElementById('btnNewProject');
  const btnDeleteProject = document.getElementById('btnDeleteProject');

  // 统计指标卡
  const statTotal = document.getElementById('statTotal');
  const statClicked = document.getElementById('statClicked');
  const statImage = document.getElementById('statImage');
  const statExposed = document.getElementById('statExposed');

  // 控制面板设置项
  const toggleDim = document.getElementById('toggleDim');
  const selectViewArea = document.getElementById('selectViewArea');
  const inputDuration = document.getElementById('inputDuration');

  // 搜索、导出与列表
  const searchInput = document.getElementById('searchInput');
  const btnExport = document.getElementById('btnExport');
  const recordsList = document.getElementById('recordsList');
  const filteredCount = document.getElementById('filteredCount');

  // 清除操作
  const btnClearExposed = document.getElementById('btnClearExposed');
  const btnClearAll = document.getElementById('btnClearAll');

  // 全局数据状态
  let currentConfig = {
    exposureDuration: 2000,
    viewArea: 'half',
    dimViewed: false,
    isTrackingEnabled: true,
    currentProjectId: 'default'
  };

  let projectsList = [
    { id: 'default', name: '默认项目', createdAt: Date.now() }
  ];

  let allProjectRecordsMap = {};
  let currentProjectRecords = {};

  function formatTime(timestamp) {
    if (!timestamp) return '-';
    const d = new Date(timestamp);
    const pad = (n) => (n < 10 ? '0' + n : n);
    return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function getRelativeTimeString(timestamp) {
    if (!timestamp) return '-';
    const now = Date.now();
    const diffMs = now - timestamp;
    if (diffMs < 0) return '刚刚';

    const diffMin = Math.floor(diffMs / (60 * 1000));
    const diffHour = Math.floor(diffMs / (60 * 60 * 1000));

    const recordDate = new Date(timestamp);
    const today = new Date();
    const isToday = recordDate.toDateString() === today.toDateString();

    const yesterday = new Date(today);
    yesterday.setDate(today.getDate() - 1);
    const isYesterday = recordDate.toDateString() === yesterday.toDateString();

    const beforeYesterday = new Date(today);
    beforeYesterday.setDate(today.getDate() - 2);
    const isBeforeYesterday = recordDate.toDateString() === beforeYesterday.toDateString();

    if (diffMin < 1) {
      return '刚刚';
    } else if (diffMin < 60) {
      return `${diffMin}分钟前`;
    } else if (isToday) {
      return `${diffHour}小时前`;
    } else if (isYesterday) {
      return '昨天';
    } else if (isBeforeYesterday) {
      return '前天';
    } else {
      const diffDays = Math.floor(diffHour / 24);
      if (diffDays <= 7) {
        return `${diffDays}天前`;
      } else {
        const pad = (n) => (n < 10 ? '0' + n : n);
        return `${pad(recordDate.getMonth() + 1)}-${pad(recordDate.getDate())}`;
      }
    }
  }

  // ================= 数据初始化与项目管理 =================

  function loadData() {
    chrome.storage.local.get(['ppt_records_by_project', 'ppt_records', 'ppt_config', 'ppt_projects'], (res) => {
      if (res.ppt_config) {
        currentConfig = Object.assign(currentConfig, res.ppt_config);
      }

      if (res.ppt_projects && res.ppt_projects.length > 0) {
        projectsList = res.ppt_projects;
      }

      allProjectRecordsMap = res.ppt_records_by_project || {};
      const curId = currentConfig.currentProjectId || 'default';

      // 兼容旧数据
      if (!allProjectRecordsMap[curId] && res.ppt_records) {
        allProjectRecordsMap[curId] = res.ppt_records;
      }
      currentProjectRecords = allProjectRecordsMap[curId] || {};

      // 渲染项目选择器
      renderProjectSelector();

      // 渲染开始/暂停按钮状态
      updateTrackingStatusUI();

      // 渲染设置项
      toggleDim.checked = !!currentConfig.dimViewed;
      selectViewArea.value = currentConfig.viewArea || 'half';
      inputDuration.value = (currentConfig.exposureDuration || 2000) / 1000;

      // 渲染统计与列表
      renderStatsAndList();
    });
  }

  function renderProjectSelector() {
    projectSelect.innerHTML = '';
    projectsList.forEach((proj) => {
      const opt = document.createElement('option');
      opt.value = proj.id;
      opt.textContent = proj.name;
      if (proj.id === currentConfig.currentProjectId) {
        opt.selected = true;
      }
      projectSelect.appendChild(opt);
    });
  }

  function getCurrentProjectName() {
    const p = projectsList.find((item) => item.id === currentConfig.currentProjectId);
    return p ? p.name : '默认项目';
  }

  // 切换工作项目
  projectSelect.addEventListener('change', () => {
    currentConfig.currentProjectId = projectSelect.value;
    currentProjectRecords = allProjectRecordsMap[currentConfig.currentProjectId] || {};
    chrome.storage.local.set({ ppt_config: currentConfig }, () => {
      renderStatsAndList();
    });
  });

  // 新建项目
  btnNewProject.addEventListener('click', () => {
    const name = prompt('请输入新项目名称（例如：折叠屏铰链稳定性检索）：');
    if (!name || !name.trim()) return;

    const newId = 'proj_' + Date.now();
    const newProj = { id: newId, name: name.trim(), createdAt: Date.now() };

    projectsList.push(newProj);
    allProjectRecordsMap[newId] = {};
    currentConfig.currentProjectId = newId;
    currentProjectRecords = {};

    chrome.storage.local.set({
      ppt_projects: projectsList,
      ppt_config: currentConfig,
      ppt_records_by_project: allProjectRecordsMap
    }, () => {
      renderProjectSelector();
      renderStatsAndList();
    });
  });

  // 删除项目
  btnDeleteProject.addEventListener('click', () => {
    if (currentConfig.currentProjectId === 'default') {
      alert('“默认项目”无法删除。');
      return;
    }

    const curName = getCurrentProjectName();
    if (confirm(`确定要删除项目「${curName}」及其全部浏览记录吗？此操作无法撤销。`)) {
      projectsList = projectsList.filter((p) => p.id !== currentConfig.currentProjectId);
      delete allProjectRecordsMap[currentConfig.currentProjectId];

      currentConfig.currentProjectId = 'default';
      currentProjectRecords = allProjectRecordsMap['default'] || {};

      chrome.storage.local.set({
        ppt_projects: projectsList,
        ppt_config: currentConfig,
        ppt_records_by_project: allProjectRecordsMap
      }, () => {
        renderProjectSelector();
        renderStatsAndList();
      });
    }
  });

  // ================= 开始 / 停止追踪开关 =================

  function updateTrackingStatusUI() {
    if (currentConfig.isTrackingEnabled) {
      btnToggleTracking.className = 'status-btn active';
      statusBtnText.textContent = '追踪中';
    } else {
      btnToggleTracking.className = 'status-btn paused';
      statusBtnText.textContent = '已暂停';
    }
  }

  btnToggleTracking.addEventListener('click', () => {
    currentConfig.isTrackingEnabled = !currentConfig.isTrackingEnabled;
    updateTrackingStatusUI();
    chrome.storage.local.set({ ppt_config: currentConfig });
  });

  // ================= 统计与列表渲染 =================

  function renderStatsAndList() {
    const query = (searchInput.value || '').trim().toUpperCase();
    const docKeys = Object.keys(currentProjectRecords);

    let total = docKeys.length;
    let clickedCount = 0;
    let imageCount = 0;
    let exposedCount = 0;

    docKeys.forEach((key) => {
      const item = currentProjectRecords[key];
      if (item.clicked) {
        clickedCount++;
      } else if (item.imageClicked) {
        imageCount++;
      } else if (item.exposed) {
        exposedCount++;
      }
    });

    statTotal.textContent = total;
    statClicked.textContent = clickedCount;
    if (statImage) statImage.textContent = imageCount;
    statExposed.textContent = exposedCount;

    let filteredKeys = docKeys.filter((doc) => doc.includes(query));
    filteredKeys.sort((a, b) => {
      const timeA = currentProjectRecords[a].clickedAt || currentProjectRecords[a].imageClickedAt || currentProjectRecords[a].exposedAt || 0;
      const timeB = currentProjectRecords[b].clickedAt || currentProjectRecords[b].imageClickedAt || currentProjectRecords[b].exposedAt || 0;
      return timeB - timeA;
    });

    filteredCount.textContent = query
      ? `筛选匹配 (${filteredKeys.length} / ${total})`
      : `记录列表 (${total})`;

    if (filteredKeys.length === 0) {
      recordsList.innerHTML = `<div class="empty-state">${query ? '未找到匹配的公开号' : '当前项目暂无专利浏览记录'}</div>`;
      return;
    }

    let html = '';
    filteredKeys.forEach((docNum) => {
      const item = currentProjectRecords[docNum];
      let badgeClass = 'record-badge-exposed';
      let badgeText = '已扫过';
      let time = item.exposedAt;

      if (item.clicked) {
        badgeClass = 'record-badge-clicked';
        badgeText = '看详情';
        time = item.clickedAt;
      } else if (item.imageClicked) {
        badgeClass = 'record-badge-image';
        badgeText = '看附图';
        time = item.imageClickedAt;
      }

      const relTime = getRelativeTimeString(time);

      html += `
        <div class="record-row" data-doc="${docNum}">
          <div class="record-info">
            <span class="record-docnum">${docNum}</span>
            <span class="record-badge ${badgeClass}">${badgeText}</span>
          </div>
          <div class="record-actions">
            <span class="record-time" title="${formatTime(time)}">${relTime}</span>
            <button class="record-del-btn" title="删除该条记录" data-delete-doc="${docNum}">
              <svg width="12" height="12" viewBox="0 0 20 20" fill="currentColor">
                <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd" />
              </svg>
            </button>
          </div>
        </div>
      `;
    });

    recordsList.innerHTML = html;

    recordsList.querySelectorAll('[data-delete-doc]').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const docToDelete = btn.getAttribute('data-delete-doc');
        if (docToDelete && currentProjectRecords[docToDelete]) {
          delete currentProjectRecords[docToDelete];
          allProjectRecordsMap[currentConfig.currentProjectId] = currentProjectRecords;
          chrome.storage.local.set({ ppt_records_by_project: allProjectRecordsMap }, () => {
            renderStatsAndList();
          });
        }
      });
    });
  }

  // ================= 设置与配置保存 =================

  function saveConfig() {
    chrome.storage.local.set({ ppt_config: currentConfig });
  }

  toggleDim.addEventListener('change', () => {
    currentConfig.dimViewed = toggleDim.checked;
    saveConfig();
  });

  selectViewArea.addEventListener('change', () => {
    currentConfig.viewArea = selectViewArea.value;
    saveConfig();
  });

  inputDuration.addEventListener('change', () => {
    let val = parseFloat(inputDuration.value);
    if (isNaN(val) || val < 0.5) {
      val = 0.5;
      inputDuration.value = '0.5';
    }
    currentConfig.exposureDuration = Math.round(val * 1000);
    saveConfig();
  });

  searchInput.addEventListener('input', () => {
    renderStatsAndList();
  });

  // ================= 导出功能 =================

  btnExport.addEventListener('click', () => {
    const docKeys = Object.keys(currentProjectRecords);
    if (docKeys.length === 0) {
      alert('当前项目暂无任何记录可供导出。');
      return;
    }

    const curName = getCurrentProjectName();
    let csvContent = '\uFEFF公开号,主要状态,视口曝光时间,附图点击时间,详情点击时间,所属项目\n';
    docKeys.forEach((docNum) => {
      const item = currentProjectRecords[docNum];
      let status = '已视口扫过';
      if (item.clicked) status = '已看详情';
      else if (item.imageClicked) status = '已看附图';

      const exposedTime = item.exposedAt ? new Date(item.exposedAt).toLocaleString() : '';
      const imageTime = item.imageClickedAt ? new Date(item.imageClickedAt).toLocaleString() : '';
      const clickedTime = item.clickedAt ? new Date(item.clickedAt).toLocaleString() : '';
      csvContent += `"${docNum}","${status}","${exposedTime}","${imageTime}","${clickedTime}","${curName}"\n`;
    });

    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const downloadAnchor = document.createElement('a');
    downloadAnchor.href = url;
    downloadAnchor.download = `智慧芽专利追踪_${curName}_${new Date().toISOString().slice(0, 10)}.csv`;
    downloadAnchor.click();
    URL.revokeObjectURL(url);
  });

  // ================= 清理与重置操作 =================

  // 仅清除当前项目未看附图且未看详情的纯“扫过”条目
  btnClearExposed.addEventListener('click', () => {
    const docKeys = Object.keys(currentProjectRecords);
    let countToClean = 0;
    docKeys.forEach((key) => {
      const item = currentProjectRecords[key];
      if (!item.clicked && !item.imageClicked) {
        countToClean++;
      }
    });

    if (countToClean === 0) {
      alert('当前项目没有纯“仅扫过”的记录需要清理。');
      return;
    }

    if (confirm(`确定要清除当前项目这 ${countToClean} 条“仅扫过”记录吗？\n（已看附图与已看详情的记录完好保留）`)) {
      const newRecords = {};
      docKeys.forEach((key) => {
        const item = currentProjectRecords[key];
        if (item.clicked || item.imageClicked) {
          newRecords[key] = item;
        }
      });
      currentProjectRecords = newRecords;
      allProjectRecordsMap[currentConfig.currentProjectId] = currentProjectRecords;
      chrome.storage.local.set({ ppt_records_by_project: allProjectRecordsMap }, () => {
        renderStatsAndList();
      });
    }
  });

  // 清空当前项目所有记录
  btnClearAll.addEventListener('click', () => {
    const total = Object.keys(currentProjectRecords).length;
    if (total === 0) {
      alert('当前项目暂无任何记录。');
      return;
    }

    const curName = getCurrentProjectName();
    if (confirm(`警告：确定要清空「${curName}」项目的全部 ${total} 条浏览记录吗？此操作无法撤销。`)) {
      currentProjectRecords = {};
      allProjectRecordsMap[currentConfig.currentProjectId] = {};
      chrome.storage.local.set({ ppt_records_by_project: allProjectRecordsMap }, () => {
        renderStatsAndList();
      });
    }
  });

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === 'local') {
      if (changes.ppt_records_by_project) {
        allProjectRecordsMap = changes.ppt_records_by_project.newValue || {};
        currentProjectRecords = allProjectRecordsMap[currentConfig.currentProjectId] || {};
        renderStatsAndList();
      }
    }
  });

  loadData();
});
