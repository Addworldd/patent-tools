/**
 * 智慧芽专利浏览追踪助手 - Content Script
 * 终极稳定版：彻底消灭全局容器污染与表格布局坍塌
 * 1. 严格行级隔离：严禁向上匹配全页容器，每个公开号独立提取、独立打标，杜绝多行串联
 * 2. 纯净行内徽章：绝不覆写 TR/TD 等任何原网页表格的 display/height，100% 保持网页原生布局
 * 3. 详情页单篇守护：URL/Title 双重锁定，异步轮询确保左上角主号稳健呈现，绝缘辅助区域
 * 4. 附图与标题点击：精准识别，排除复选框，即时同步持久化
 */

(function () {
  'use strict';

  // 全局拦截扩展重载时的 context invalidated 提示
  window.addEventListener('error', (event) => {
    if (event && event.message && event.message.includes('Extension context invalidated')) {
      event.preventDefault();
      event.stopImmediatePropagation();
      return true;
    }
  }, true);

  if (window.__PATSNAP_TRACKER_LOADED__) return;
  window.__PATSNAP_TRACKER_LOADED__ = true;

  // 控制台彩色运行日志
  function logInfo(msg, ...args) {
    console.log(
      `%c[智慧芽追踪助手]%c ${msg}`,
      'background:#4f46e5;color:#fff;padding:2px 6px;border-radius:3px;font-weight:bold;font-size:11px;',
      'color:#334155;font-weight:600;font-size:11px;',
      ...args
    );
  }

  // 全局配置项（从 storage 同步）
  let config = {
    exposureDuration: 2000,     // 曝光停留有效判定时长(ms)，默认 2.0 秒
    viewArea: 'half',           // 'half' (高于中线才计时) 或 'full'
    dimViewed: false,           // 是否半透明淡化
    isTrackingEnabled: true,    // 开始/暂停开关 (默认为 true)
    currentProjectId: 'default' // 当前工作项目 ID
  };

  let currentProjectRecords = {};
  let allProjectRecordsMap = {};
  let projectsList = [{ id: 'default', name: '默认项目', createdAt: Date.now() }];
  const exposureTimers = new Map();

  // 核心会话防呆机制：进入新页面/切换检索式必须确认项目后才开始记录，翻页静默继续
  let isSessionConfirmed = false;
  let confirmedQueryKey = null;
  let currentQueryKey = '';

  // 合法专利国别/组织代码
  const VALID_COUNTRY_CODES = new Set([
    'CN', 'US', 'EP', 'WO', 'JP', 'KR', 'DE', 'GB', 'FR', 'TW', 'HK',
    'RU', 'CA', 'AU', 'CH', 'IT', 'ES', 'NL', 'SE', 'SG', 'BR', 'IL',
    'IN', 'AT', 'FI', 'DK', 'NO', 'BE', 'NZ', 'MX', 'ZA', 'IE', 'PL', 'PT', 'BY', 'IS'
  ]);

  const EXCLUDED_SYNTAX_WORDS = new Set(['TO', 'OR', 'AND', 'NOT', 'XOR']);

  const DOC_NUM_REGEX = /\b([A-Z]{2})\s*[-/]?\s*([0-9]{4,14}|[0-9]{4}[A-Z0-9]{4,10})\s*[-/]?\s*([A-Z][0-9]?)?\b/i;

  function normalizeDocNum(str) {
    if (!str) return '';
    return str.replace(/[\s\-_/]/g, '').toUpperCase();
  }

  function extractDocNum(text) {
    if (!text || typeof text !== 'string') return null;

    // 排除中国专利申请号（形如 CN201821120025.5 带小数点的校验码）
    if (/[A-Z]{2}\s*[0-9]{8,14}\.[\dXx]/i.test(text)) {
      return null;
    }

    const match = text.match(DOC_NUM_REGEX);
    if (!match) return null;

    const countryCode = match[1].toUpperCase();

    if (EXCLUDED_SYNTAX_WORDS.has(countryCode)) {
      return null;
    }

    if (!VALID_COUNTRY_CODES.has(countryCode)) {
      return null;
    }

    return normalizeDocNum(match[0]);
  }

  function isExtensionValid() {
    try {
      return typeof chrome !== 'undefined' && !!chrome.runtime && !!chrome.runtime.id;
    } catch (e) {
      return false;
    }
  }

  function safeStorageGet(keys, callback) {
    if (!isExtensionValid()) {
      if (callback) {
        try { callback({}); } catch (e) {}
      }
      return;
    }
    try {
      chrome.storage.local.get(keys, (res) => {
        if (!isExtensionValid() || (chrome.runtime && chrome.runtime.lastError)) {
          if (callback) { try { callback({}); } catch (e) {} }
          return;
        }
        try { callback(res || {}); } catch (e) {}
      });
    } catch (e) {
      if (callback) { try { callback({}); } catch (e) {} }
    }
  }

  function safeStorageSet(data, callback) {
    if (!isExtensionValid()) {
      if (callback) { try { callback(); } catch (e) {} }
      return;
    }
    try {
      chrome.storage.local.set(data, () => {
        if (callback) {
          try { callback(); } catch (e) {}
        }
      });
    } catch (e) {
      if (callback) { try { callback(); } catch (e) {} }
    }
  }

  // ================= 页面类型与检索式精准判定 =================

  function isViewingPatentDetail() {
    const url = window.location.href.toLowerCase();
    if (url.includes('/patent-view/') || url.includes('/doc-view/')) {
      return true;
    }
    if (document.querySelector('.patent-view-container, .patent-detail, #patent-detail')) {
      return true;
    }
    return false;
  }

  function getSearchFormulaText() {
    // 智慧芽智能检索与高级检索的主输入框（通常为富文本容器）
    const el = document.querySelector('.editor-container, .div-textarea, [class*="search-formula"]');
    if (el) {
      const txt = (el.innerText || el.textContent || '').trim();
      if (txt) return txt;
    }
    const input = document.querySelector('input.search-input, input[placeholder*="搜索"], input[placeholder*="检索"], input[placeholder*="输入"]');
    if (input) {
      const val = (input.value || '').trim();
      if (val) return val;
    }
    return '';
  }

  function getSearchQueryKey() {
    try {
      const url = new URL(window.location.href);

      // 1. 详情页判定
      if (isViewingPatentDetail()) {
        const patentId = url.searchParams.get('patentId') || url.searchParams.get('id') || '';
        return 'detail:' + (patentId || url.pathname);
      }

      // 2. 列表页：归一化路径，去除翻页与视图类型的差异
      // 常见路径：/search/result/standard/1, /search/result/table/2, /search/result/image/3 等
      let normPath = url.pathname.replace(/\/(standard|table|image|split)\/\d+/i, '/view');
      normPath = normPath.replace(/\/\d+$/, ''); // 去掉末尾纯数字页码

      // 提取核心检索 query 参数，排除掉纯翻页/分页/临时随机参数
      const searchParams = new URLSearchParams(url.search);
      const pageParams = new Set([
        'page', 'p', 'start', 'offset', 'page_size', 'rows',
        'view_type', 'view', 'sort', 'sort_order', 'order',
        '_t', 't', 'random', '_r'
      ]);

      const relevantParams = [];
      for (const [k, v] of searchParams.entries()) {
        if (!pageParams.has(k.toLowerCase())) {
          relevantParams.push(`${k}=${v}`);
        }
      }
      relevantParams.sort();

      // 核心融合：获取搜索框内可见的检索文本（最灵敏实时的用户意图真实状态）
      const formulaText = getSearchFormulaText();
      const formulaPart = formulaText ? `formula=${encodeURIComponent(formulaText)}` : '';

      return `${normPath}?${formulaPart}&${relevantParams.join('&')}`;
    } catch (e) {
      return window.location.pathname;
    }
  }

  function isInsideSearchInputZone(el) {
    if (!el || el.nodeType !== Node.ELEMENT_NODE) return false;
    if (['INPUT', 'TEXTAREA'].includes(el.tagName)) return true;
    if (el.isContentEditable) return true;
    return !!el.closest('[class*="search-input"], [class*="search-formula"], [class*="search-bar"], [class*="search-box"], [class*="syntax-highlighter"], .search-header');
  }

  function isDetailAuxiliaryForbiddenZone(el) {
    if (!el || el.nodeType !== Node.ELEMENT_NODE) return false;
    return !!el.closest('[class*="timeline"], [class*="time-line"], [class*="drawer"], [class*="side-list"], [class*="right-list"], [class*="date-box"], [class*="patent-drawer"]');
  }

  function formatTime(timestamp) {
    if (!timestamp) return '';
    const d = new Date(timestamp);
    const pad = (n) => (n < 10 ? '0' + n : n);
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function getRelativeTimeString(timestamp) {
    if (!timestamp) return '';
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

  // ================= 存储管理 =================

  function initStorage(callback) {
    safeStorageGet(['ppt_records_by_project', 'ppt_records', 'ppt_config', 'ppt_projects'], (res) => {
      if (res && res.ppt_config) {
        config = Object.assign(config, res.ppt_config);
      }
      config.isTrackingEnabled = (config.isTrackingEnabled !== false);
      config.currentProjectId = config.currentProjectId || 'default';

      if (res && res.ppt_projects && Array.isArray(res.ppt_projects) && res.ppt_projects.length > 0) {
        projectsList = res.ppt_projects;
      }

      allProjectRecordsMap = res.ppt_records_by_project || {};
      const curId = config.currentProjectId;

      if (!allProjectRecordsMap[curId] && res && res.ppt_records) {
        allProjectRecordsMap[curId] = res.ppt_records;
      }
      currentProjectRecords = allProjectRecordsMap[curId] || {};

      logInfo(`已就绪 | 状态: ${config.isTrackingEnabled ? '追踪中' : '已暂停'} | 当前项目: ${config.currentProjectId} | 历史记录数: ${Object.keys(currentProjectRecords).length}`);

      if (callback) callback();
    });
  }

  let saveDebounceTimer = null;
  function persistCurrentProjectRecords(immediate = false) {
    if (!isExtensionValid()) return;

    const doSave = () => {
      const curId = config.currentProjectId || 'default';
      allProjectRecordsMap[curId] = currentProjectRecords;
      safeStorageSet({ ppt_records_by_project: allProjectRecordsMap });
    };

    if (immediate) {
      if (saveDebounceTimer) clearTimeout(saveDebounceTimer);
      doSave();
    } else {
      if (saveDebounceTimer) clearTimeout(saveDebounceTimer);
      saveDebounceTimer = setTimeout(doSave, 300);
    }
  }

  function recordExpose(docNum) {
    if (!docNum || !config.isTrackingEnabled || !isSessionConfirmed) return;
    const now = Date.now();
    let record = currentProjectRecords[docNum];

    if (!record) {
      record = {
        exposed: true,
        exposedAt: now,
        imageClicked: false,
        imageClickedAt: null,
        clicked: false,
        clickedAt: null
      };
      currentProjectRecords[docNum] = record;
      persistCurrentProjectRecords(false);
      logInfo(`👀 视口曝光: ${docNum}`);
    } else if (!record.exposed) {
      record.exposed = true;
      record.exposedAt = now;
      currentProjectRecords[docNum] = record;
      persistCurrentProjectRecords(false);
      logInfo(`👀 补充曝光: ${docNum}`);
    }

    updateBadgesForDoc(docNum, record);
  }

  function recordImageClick(docNum) {
    if (!docNum || !config.isTrackingEnabled || !isSessionConfirmed) return;
    const now = Date.now();
    let record = currentProjectRecords[docNum] || {
      exposed: true,
      exposedAt: now,
      clicked: false,
      clickedAt: null
    };

    record.imageClicked = true;
    record.imageClickedAt = now;
    currentProjectRecords[docNum] = record;
    persistCurrentProjectRecords(true);
    logInfo(`🖼️ 看附图触发: ${docNum}`);

    updateBadgesForDoc(docNum, record);
  }

  function recordClick(docNum) {
    if (!docNum || !config.isTrackingEnabled || !isSessionConfirmed) return;
    const now = Date.now();
    let record = currentProjectRecords[docNum] || {
      exposed: true,
      exposedAt: now,
      imageClicked: false,
      imageClickedAt: null
    };

    record.clicked = true;
    record.clickedAt = now;
    currentProjectRecords[docNum] = record;
    persistCurrentProjectRecords(true);
    logInfo(`✨ 看详情触发: ${docNum}`);

    updateBadgesForDoc(docNum, record);
  }

  // ================= 徽章渲染与更新 =================

  function createBadgeElement(record, isMain = false) {
    const badge = document.createElement('span');
    badge.className = isMain ? 'ppt-badge ppt-badge-main' : 'ppt-badge';

    if (record.clicked) {
      badge.classList.add('ppt-badge-clicked');
      const relTime = getRelativeTimeString(record.clickedAt);
      badge.innerHTML = `
        <svg viewBox="0 0 20 20" fill="currentColor">
          <path d="M10 12a2 2 0 100-4 2 2 0 000 4z"/>
          <path fill-rule="evenodd" d="M.458 10C1.732 5.943 5.522 3 10 3s8.268 2.943 9.542 7c-1.274 4.057-5.064 7-9.542 7S1.732 14.057.458 10zM14 10a4 4 0 11-8 0 4 4 0 018 0z" clip-rule="evenodd"/>
        </svg>
        <span>${relTime}看详情</span>
      `;
      const timeStr = formatTime(record.clickedAt);
      badge.setAttribute('data-tooltip', `已点开详情：${timeStr}`);
    } else if (record.imageClicked) {
      badge.classList.add('ppt-badge-image');
      const relTime = getRelativeTimeString(record.imageClickedAt);
      badge.innerHTML = `
        <svg viewBox="0 0 20 20" fill="currentColor">
          <path fill-rule="evenodd" d="M4 3a2 2 0 00-2 2v10a2 2 0 002 2h12a2 2 0 002-2V5a2 2 0 00-2-2H4zm12 12H4l4-8 3 6 2-4 3 6z" clip-rule="evenodd"/>
        </svg>
        <span>${relTime}看附图</span>
      `;
      const timeStr = formatTime(record.imageClickedAt);
      badge.setAttribute('data-tooltip', `已查看附图大图：${timeStr}`);
    } else if (record.exposed) {
      badge.classList.add('ppt-badge-exposed');
      const relTime = getRelativeTimeString(record.exposedAt);
      badge.innerHTML = `
        <svg viewBox="0 0 20 20" fill="currentColor">
          <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"/>
        </svg>
        <span>${relTime}已扫过</span>
      `;
      const timeStr = formatTime(record.exposedAt);
      badge.setAttribute('data-tooltip', `屏幕曝光扫过：${timeStr}`);
    }

    return badge;
  }

  function renderBadgeForAnchor(anchor, record, isMain = false) {
    if (!anchor || !record) return;

    if (isInsideSearchInputZone(anchor) || isDetailAuxiliaryForbiddenZone(anchor)) {
      return;
    }

    // 仅查找与当前 anchor 紧邻的下一个兄弟徽章并替换，绝不使用 parent.querySelector 误删其它行的徽章！
    const nextElem = anchor.nextElementSibling;
    if (nextElem && nextElem.classList.contains('ppt-badge')) {
      nextElem.remove();
    }

    if (record.exposed || record.imageClicked || record.clicked) {
      const badge = createBadgeElement(record, isMain);
      anchor.insertAdjacentElement('afterend', badge);
    }
  }

  function updateBadgesForDoc(docNum, record) {
    if (!docNum || !record) return;

    // 1. 详情页左上角主号
    if (isViewingPatentDetail()) {
      const mainAnchor = document.querySelector('[data-ppt-main-title]');
      if (mainAnchor) {
        renderBadgeForAnchor(mainAnchor, record, true);
      }
    }

    // 2. 列表页：精准匹配属于该 docNum 的独立锚点
    const anchors = document.querySelectorAll(`[data-ppt-doc-anchor][data-ppt-doc="${docNum}"]`);
    anchors.forEach((anchor) => {
      renderBadgeForAnchor(anchor, record, false);
    });
  }

  // ================= 严格单条行级卡片查找器（杜绝全页容器污染） =================

  function findStrictPatentCard(el) {
    if (!el || el.nodeType !== Node.ELEMENT_NODE) return null;

    // 1. 完整专利卡片优先（排除仅有 24px 的 patent-card__no 纯公开号小行）
    const fullCard = el.closest('.patent-card:not([class*="__no"]), [class*="patent-card-wrap"], .vue-recycle-scroller__item-view, .pt-table__row-collection, tr');
    if (fullCard) return fullCard;

    // 2. 其它单行或条目容器
    const directRow = el.closest('.patent-card, .search-result-item, .pt-table__row, [class*="search-result-item"]');
    if (directRow) return directRow;

    if (el.tagName === 'DIV' && el.parentElement && el.parentElement.tagName === 'TBODY') {
      return el;
    }

    return el.closest('li') || null;
  }

  function bindPatentItem(anchorElem, docNum) {
    if (!anchorElem || !docNum) return;
    if (isInsideSearchInputZone(anchorElem) || isDetailAuxiliaryForbiddenZone(anchorElem)) return;

    // 确保当前锚点标记自己独属的公开号
    anchorElem.setAttribute('data-ppt-doc-anchor', 'true');
    anchorElem.setAttribute('data-ppt-doc', docNum);

    const cardElem = findStrictPatentCard(anchorElem);
    if (cardElem) {
      cardElem.setAttribute('data-ppt-bound', 'true');
      cardElem.setAttribute('data-ppt-doc', docNum);

      if (intersectionObserver && config.isTrackingEnabled) {
        intersectionObserver.observe(cardElem);
      }
    }

    const record = currentProjectRecords[docNum];
    if (record) {
      renderBadgeForAnchor(anchorElem, record, false);
    }
  }

  // ================= 视口曝光检测 =================

  let intersectionObserver = null;

  function isElementInValidExposureZone(target, entry) {
    if (!entry.isIntersecting) return false;

    if (config.viewArea === 'half') {
      const rect = target.getBoundingClientRect();
      const halfScreenY = window.innerHeight * 0.65;
      return rect.top <= halfScreenY && rect.bottom >= window.innerHeight * 0.05;
    } else {
      return entry.intersectionRatio >= 0.3;
    }
  }

  function setupIntersectionObserver() {
    if (intersectionObserver) {
      intersectionObserver.disconnect();
    }

    exposureTimers.forEach((item) => clearTimeout(item.timer));
    exposureTimers.clear();

    if (!config.isTrackingEnabled) return;

    const isHalf = config.viewArea === 'half';
    const observerOptions = {
      root: null,
      rootMargin: isHalf ? '0px 0px -40% 0px' : '0px',
      threshold: [0, 0.1, 0.25, 0.5, 0.75, 1.0]
    };

    intersectionObserver = new IntersectionObserver((entries) => {
      if (!config.isTrackingEnabled) return;

      entries.forEach((entry) => {
        const target = entry.target;
        const docNum = target.getAttribute('data-ppt-doc');
        if (!docNum) return;

        const isValid = isElementInValidExposureZone(target, entry);

        if (isValid) {
          if (!exposureTimers.has(target)) {
            const duration = Math.max(200, config.exposureDuration || 2000);
            const timer = setTimeout(() => {
              recordExpose(docNum);
              exposureTimers.delete(target);
            }, duration);

            exposureTimers.set(target, { timer, docNum });
          }
        } else {
          if (exposureTimers.has(target)) {
            const item = exposureTimers.get(target);
            clearTimeout(item.timer);
            exposureTimers.delete(target);
          }
        }
      });
    }, observerOptions);

    document.querySelectorAll('[data-ppt-bound]').forEach((cardElem) => {
      intersectionObserver.observe(cardElem);
    });
  }

  // ================= 极速靶向 DOM 扫描 =================

  /**
   * 详情页核心策略：左上角主公开号 100% 稳健打标与呈现
   */
  function handleDetailPageSingleMainPatent() {
    let mainDocNum = extractDocNum(document.title) || extractDocNum(window.location.href);

    let mainAnchorElem = null;

    const mainCandidates = document.querySelectorAll(
      '.patent-title-wrap h1, .patent-title-wrap h2, .patent-title-wrap span, .detail-header [class*="num"], .detail-top [class*="num"], h1, h2, h3, a, span, div'
    );

    for (const el of mainCandidates) {
      if (el.classList.contains('ppt-badge')) continue;
      if (isInsideSearchInputZone(el)) continue;
      if (isDetailAuxiliaryForbiddenZone(el)) continue;

      const text = (el.innerText || el.textContent || '').trim();
      const detected = extractDocNum(text);
      if (detected) {
        const rect = el.getBoundingClientRect();
        if (rect.top >= 0 && rect.top < 260 && rect.left >= 0 && rect.left < 650 && rect.width > 0) {
          mainDocNum = detected;
          mainAnchorElem = el;
          break;
        }
      }
    }

    if (mainDocNum) {
      recordClick(mainDocNum);

      if (mainAnchorElem) {
        mainAnchorElem.setAttribute('data-ppt-main-title', 'true');
        const record = currentProjectRecords[mainDocNum] || {
          clicked: true,
          clickedAt: Date.now()
        };
        renderBadgeForAnchor(mainAnchorElem, record, true);
        logInfo(`详情页已标定主公开号: ${mainDocNum}`);
      }
    }

    document.querySelectorAll('.ppt-badge:not(.ppt-badge-main)').forEach((b) => b.remove());
  }

  let isScanning = false;
  let scanTimer = null;

  function scanPage() {
    if (scanTimer) clearTimeout(scanTimer);
    scanTimer = setTimeout(() => {
      if (isScanning) return;
      isScanning = true;

      try {
        if (isViewingPatentDetail()) {
          handleDetailPageSingleMainPatent();
          if (intersectionObserver) {
            intersectionObserver.disconnect();
          }
          isScanning = false;
          return;
        }

        if (!intersectionObserver && config.isTrackingEnabled) {
          setupIntersectionObserver();
        }

        // 仅锁定真正的链接或叶子公开号节点，绝对不扫描整个 TD 或大容器！
        const targetSelector = 'a.pn-cell-popover, [data-link-type="PN"], [data-column*="PN"] a, a[class*="number"], [class*="doc-num"] a, td a';
        const candidateNodes = document.querySelectorAll(targetSelector);

        candidateNodes.forEach((node) => {
          if (node.hasAttribute('data-ppt-doc-anchor')) return;
          if (isInsideSearchInputZone(node) || isDetailAuxiliaryForbiddenZone(node)) return;

          const text = (node.innerText || node.textContent || '').trim();
          if (!text || text.length > 35) return;

          const docNum = extractDocNum(text);
          if (docNum) {
            bindPatentItem(node, docNum);
          }
        });
      } finally {
        isScanning = false;
      }
    }, 250);
  }

  // ================= 附图与公开号智能双向定位器 =================

  function findDocNumForImageClick(target) {
    if (!target) return null;

    const card = findStrictPatentCard(target);
    if (card) {
      const boundDoc = card.getAttribute('data-ppt-doc');
      if (boundDoc) return boundDoc;

      const pnAnchor = card.querySelector('a.pn-cell-popover, [data-link-type="PN"], [data-column*="PN"] a, [class*="pn-cell"] a');
      if (pnAnchor) {
        const doc = extractDocNum(pnAnchor.textContent);
        if (doc) return doc;
      }
    }

    const link = target.closest('a');
    if (link && link.href) {
      const fromHref = extractDocNum(link.href);
      if (fromHref) return fromHref;
    }

    return null;
  }

  // ================= 严格判定：操作意图与排除规则 =================

  function isCheckboxClick(target) {
    if (!target) return false;
    return !!(
      target.tagName === 'INPUT' ||
      target.closest('input[type="checkbox"], input[type="radio"]') ||
      target.closest('.pt-checkbox, .el-checkbox, [class*="checkbox"], [class*="check-inner"], [class*="selection"]') ||
      target.closest('.pt-table__cell--selection, .pt-table__column--selection')
    );
  }

  function isGenuineImageTarget(target) {
    if (isCheckboxClick(target)) return false;

    if (target.closest('.nail-box, .nail-box__img, .image-box, .image-box-wrapper, [class*="nail-box"]')) {
      return true;
    }

    const appendixRow = target.closest('.pt-table__row--appendix, [class*="row--appendix"]');
    if (appendixRow) {
      if (target.tagName === 'IMG' || target.closest('img') || target.closest('.nail-box')) {
        return true;
      }
      const text = (target.innerText || '').trim();
      if (text.includes('查看所有') || text.includes('详细附图') || text.includes('查看附图') || text.includes('全图')) {
        return true;
      }
    }

    if (target.tagName === 'IMG' || target.closest('img')) {
      const img = target.tagName === 'IMG' ? target : target.closest('img');
      const src = img.src || '';
      if (src.includes('thumbnail') || src.includes('patent') || src.includes('zhihuiya') || src.includes('patsnap')) {
        return true;
      }
      if (img.className.includes('nail') || img.className.includes('image')) {
        return true;
      }
    }

    return false;
  }

  function isGenuineDetailTarget(target) {
    if (isCheckboxClick(target)) return false;

    // 核心门禁：必须是真正的超链接 A 标签（或其内部文字/图标）！空白区域的 TD/DIV 绝不触发！
    const link = target.closest('a');
    if (!link) return false;

    // A. 专利公开号本体链接
    if (
      link.hasAttribute('data-ppt-doc-anchor') ||
      link.classList.contains('pn-cell-popover') ||
      link.getAttribute('data-link-type') === 'PN'
    ) {
      return true;
    }
    if (extractDocNum(link.textContent.trim())) {
      return true;
    }

    // B. 专利标题链接
    if (
      link.classList.contains('title-cell-popover') ||
      link.getAttribute('data-link-type') === 'TI' ||
      link.closest('[class*="title-cell"]') ||
      (link.className && typeof link.className === 'string' && link.className.includes('title'))
    ) {
      return true;
    }

    return false;
  }

  // ================= 全局事件捕获 =================

  function setupGlobalClickCapture() {
    window.addEventListener('click', (e) => {
      const target = e.target;
      if (!target || target.nodeType !== Node.ELEMENT_NODE) return;
      if (target.closest('.ppt-badge')) return;
      if (isInsideSearchInputZone(target) || isDetailAuxiliaryForbiddenZone(target)) return;

      if (isViewingPatentDetail() && !target.closest('[data-ppt-main-title]')) return;
      if (!config.isTrackingEnabled) return;

      if (isCheckboxClick(target)) {
        return;
      }

      // A. 附图点击捕获
      if (isGenuineImageTarget(target)) {
        const docNum = findDocNumForImageClick(target);
        if (docNum) {
          recordImageClick(docNum);
          return;
        }
      }

      // B. 点击公开号 或 点击专利名称/标题链接（看详情）
      if (isGenuineDetailTarget(target)) {
        const link = target.closest('a');
        if (!link) return;

        let docNum = null;

        // 1. 如果点击的是公开号链接本身
        if (link.hasAttribute('data-ppt-doc')) {
          docNum = link.getAttribute('data-ppt-doc');
        } else {
          docNum = extractDocNum(link.textContent.trim());
        }

        // 2. 如果点击的是标题链接，从当前单行容器中定位对应的公开号
        if (!docNum) {
          const cardElem = findStrictPatentCard(link);
          if (cardElem) {
            const pnAnchor = cardElem.querySelector('[data-ppt-doc-anchor], a.pn-cell-popover, [data-link-type="PN"]');
            if (pnAnchor) {
              docNum = pnAnchor.getAttribute('data-ppt-doc') || extractDocNum(pnAnchor.textContent.trim());
            }
            if (!docNum && cardElem.hasAttribute('data-ppt-doc')) {
              docNum = cardElem.getAttribute('data-ppt-doc');
            }
          }
        }

        if (docNum) {
          recordClick(docNum);
        }
      }
    }, true);
  }

  // ================= 顶端项目确认防呆横幅与常驻小胶囊 =================

  function getProjectName(projId) {
    const p = projectsList.find((item) => item.id === projId);
    return p ? p.name : '默认项目';
  }

  function renderProjectBannerAndPill() {
    let banner = document.getElementById('ppt-project-banner');
    if (banner) {
      banner.remove();
    }
    let pill = document.getElementById('ppt-mini-pill');
    if (pill) {
      pill.remove();
    }

    // 创建顶端项目确认横幅
    banner = document.createElement('div');
    banner.id = 'ppt-project-banner';
    banner.className = 'ppt-banner-hidden';
    banner.innerHTML = `
      <div class="ppt-banner-icon">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="10"></circle>
          <polyline points="12 6 12 12 16 14"></polyline>
        </svg>
      </div>
      <span class="ppt-banner-text">智慧芽追踪助手：请确认当前检索归属的项目：</span>
      <select class="ppt-banner-select" id="ppt-banner-project-select"></select>
      <button type="button" class="ppt-banner-btn-new" id="ppt-banner-btn-new">+新建</button>
      <button type="button" class="ppt-banner-btn-confirm" id="ppt-banner-btn-confirm">确认并开始追踪</button>
      <button type="button" class="ppt-banner-btn-pause" id="ppt-banner-btn-pause">本次暂停</button>
    `;
    document.body.appendChild(banner);

    // 创建右上角常驻极简小胶囊
    pill = document.createElement('div');
    pill.id = 'ppt-mini-pill';
    pill.className = 'ppt-pill-hidden';
    pill.title = '点击切换项目或管理追踪状态';
    pill.innerHTML = `
      <span class="ppt-pill-dot"></span>
      <span class="ppt-pill-name">默认项目</span>
      <span class="ppt-pill-action">[切换]</span>
    `;
    document.body.appendChild(pill);

    // 绑定事件
    const select = document.getElementById('ppt-banner-project-select');
    const btnNew = document.getElementById('ppt-banner-btn-new');
    const btnConfirm = document.getElementById('ppt-banner-btn-confirm');
    const btnPause = document.getElementById('ppt-banner-btn-pause');

    function refreshSelectOptions() {
      select.innerHTML = '';
      projectsList.forEach((p) => {
        const opt = document.createElement('option');
        opt.value = p.id;
        opt.textContent = p.name;
        if (p.id === config.currentProjectId) {
          opt.selected = true;
        }
        select.appendChild(opt);
      });
    }
    refreshSelectOptions();

    // 新建项目
    btnNew.addEventListener('click', () => {
      const name = prompt('请输入新项目名称（例如：折叠屏铰链稳定性检索）：');
      if (!name || !name.trim()) return;

      const newId = 'proj_' + Date.now();
      const newProj = { id: newId, name: name.trim(), createdAt: Date.now() };
      projectsList.push(newProj);
      allProjectRecordsMap[newId] = {};
      config.currentProjectId = newId;
      currentProjectRecords = {};

      safeStorageSet({
        ppt_projects: projectsList,
        ppt_config: config,
        ppt_records_by_project: allProjectRecordsMap
      }, () => {
        refreshSelectOptions();
        logInfo(`新建并选中项目: ${newProj.name} (${newId})`);
      });
    });

    // 确认并开始追踪
    btnConfirm.addEventListener('click', () => {
      const selectedId = select.value || 'default';
      config.currentProjectId = selectedId;
      config.isTrackingEnabled = true;

      currentProjectRecords = allProjectRecordsMap[selectedId] || {};
      isSessionConfirmed = true;
      confirmedQueryKey = currentQueryKey;

      logInfo(`项目已确认: ${getProjectName(selectedId)}，开始记录当前检索式浏览行为`);
      updateBannerAndPillUI();

      safeStorageSet({
        ppt_config: config
      });

      // 刷新页面已有徽章以适配新项目的数据
      document.querySelectorAll('[data-ppt-doc-anchor]').forEach((anchor) => {
        const docNum = anchor.getAttribute('data-ppt-doc');
        const record = currentProjectRecords[docNum];
        if (record) {
          renderBadgeForAnchor(anchor, record, false);
        } else {
          const oldBadge = anchor.nextElementSibling;
          if (oldBadge && oldBadge.classList.contains('ppt-badge')) {
            oldBadge.remove();
          }
        }
      });

      // 立即触发扫描
      scanPage();
    });

    // 本次暂停
    btnPause.addEventListener('click', () => {
      config.isTrackingEnabled = false;
      isSessionConfirmed = true; // 用户明确选择暂停，不再弹出打扰
      safeStorageSet({ ppt_config: config }, () => {
        logInfo('用户选择本次暂停追踪');
        updateBannerAndPillUI();
      });
    });

    // 点击 mini-pill 重新呼出确认条以随时切换项目
    pill.addEventListener('click', () => {
      isSessionConfirmed = false;
      updateBannerAndPillUI();
    });
  }

  function updateBannerAndPillUI() {
    const banner = document.getElementById('ppt-project-banner');
    const pill = document.getElementById('ppt-mini-pill');
    if (!banner || !pill) return;

    // 刷新下拉列表选择状态
    const select = document.getElementById('ppt-banner-project-select');
    if (select) {
      select.innerHTML = '';
      projectsList.forEach((p) => {
        const opt = document.createElement('option');
        opt.value = p.id;
        opt.textContent = p.name;
        if (p.id === config.currentProjectId) {
          opt.selected = true;
        }
        select.appendChild(opt);
      });
    }

    // 刷新 mini-pill 文本与状态
    const pillDot = pill.querySelector('.ppt-pill-dot');
    const pillName = pill.querySelector('.ppt-pill-name');
    if (pillName) {
      pillName.textContent = getProjectName(config.currentProjectId);
    }
    if (pillDot) {
      if (!config.isTrackingEnabled) {
        pillDot.classList.add('paused');
      } else {
        pillDot.classList.remove('paused');
      }
    }

    if (!isSessionConfirmed) {
      // 门禁未确认：展示悬浮条，隐藏 mini-pill
      banner.classList.remove('ppt-banner-hidden');
      pill.classList.add('ppt-pill-hidden');
    } else {
      // 门禁已确认：隐藏悬浮条，展示 mini-pill
      banner.classList.add('ppt-banner-hidden');
      pill.classList.remove('ppt-pill-hidden');
    }
  }

  // ================= 检索式切换感知与翻页免打扰 =================

  function checkQueryAndSession() {
    const newQuery = getSearchQueryKey();
    if (newQuery !== currentQueryKey) {
      logInfo(`检索式/页面变动感知: 旧=${currentQueryKey} -> 新=${newQuery}`);
      currentQueryKey = newQuery;

      // 如果之前已经有确认过的检索式，且新的检索式与确认过的不同，触发防呆
      if (confirmedQueryKey && newQuery !== confirmedQueryKey) {
        logInfo('⚠️ 检索式已变更，触发防呆门禁，等待用户确认工作项目');
        isSessionConfirmed = false;
      }
      updateBannerAndPillUI();
    }
  }

  function setupSearchActionInterception() {
    // 监听在搜索输入框中按回车键 (Enter)
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        const target = e.target;
        if (target && (target.closest('.editor-container, .div-textarea, [class*="search-input"], [class*="search-formula"], [class*="smart_search"], .ai-common-smart-search'))) {
          logInfo('检测到用户在搜索框按下 Enter 键，执行检索式变动即时核验');
          setTimeout(checkQueryAndSession, 100);
          setTimeout(checkQueryAndSession, 400);
          setTimeout(checkQueryAndSession, 1000);
        }
      }
    }, true);

    // 监听点击搜索/放大镜按钮 (.field-search-button 等)
    document.addEventListener('click', (e) => {
      const target = e.target;
      if (!target) return;
      const btn = target.closest('.field-search-button, [class*="search-button"], .srfb__search, [class*="smart-search-right"], .search-btn, button[type="submit"]');
      if (btn) {
        logInfo('检测到用户点击搜索按钮，执行检索式变动即时核验');
        setTimeout(checkQueryAndSession, 100);
        setTimeout(checkQueryAndSession, 400);
        setTimeout(checkQueryAndSession, 1000);
      }
    }, true);
  }

  function setupRouteListeners() {
    currentQueryKey = getSearchQueryKey();

    // 劫持 history.pushState 和 history.replaceState
    const rawPushState = history.pushState;
    history.pushState = function (...args) {
      const ret = rawPushState.apply(this, args);
      setTimeout(checkQueryAndSession, 100);
      return ret;
    };

    const rawReplaceState = history.replaceState;
    history.replaceState = function (...args) {
      const ret = rawReplaceState.apply(this, args);
      setTimeout(checkQueryAndSession, 100);
      return ret;
    };

    window.addEventListener('popstate', () => {
      setTimeout(checkQueryAndSession, 100);
    });

    window.addEventListener('hashchange', () => {
      setTimeout(checkQueryAndSession, 100);
    });

    // 300ms 轻量 interval 检查 URL / 搜索公式变动（双重防漏）
    setInterval(() => {
      checkQueryAndSession();
    }, 300);
  }

  // ================= 监听与响应配置变动与项目切换 =================

  if (isExtensionValid()) {
    try {
      chrome.storage.onChanged.addListener((changes, areaName) => {
        if (!isExtensionValid() || areaName !== 'local') return;

        let needReRender = false;

        if (changes.ppt_projects) {
          projectsList = changes.ppt_projects.newValue || [{ id: 'default', name: '默认项目', createdAt: Date.now() }];
          updateBannerAndPillUI();
        }

        if (changes.ppt_config) {
          const oldConfig = config;
          config = Object.assign(config, changes.ppt_config.newValue || {});
          config.isTrackingEnabled = (config.isTrackingEnabled !== false);

          if (oldConfig.currentProjectId !== config.currentProjectId) {
            const curId = config.currentProjectId || 'default';
            currentProjectRecords = allProjectRecordsMap[curId] || {};
            needReRender = true;
            logInfo(`切换至项目: ${curId}`);
          }

          if (oldConfig.isTrackingEnabled !== config.isTrackingEnabled) {
            logInfo(`追踪状态变更为: ${config.isTrackingEnabled ? '运行中' : '已暂停'}`);
            if (config.isTrackingEnabled) {
              setupIntersectionObserver();
              scanPage();
            } else if (intersectionObserver) {
              intersectionObserver.disconnect();
              exposureTimers.forEach((item) => clearTimeout(item.timer));
              exposureTimers.clear();
            }
          }

          if (oldConfig.viewArea !== config.viewArea) {
            setupIntersectionObserver();
          }

          updateBannerAndPillUI();
        }

        if (changes.ppt_records_by_project) {
          allProjectRecordsMap = changes.ppt_records_by_project.newValue || {};
          const curId = config.currentProjectId || 'default';
          currentProjectRecords = allProjectRecordsMap[curId] || {};
          needReRender = true;
        }

        if (needReRender) {
          if (isViewingPatentDetail()) {
            handleDetailPageSingleMainPatent();
          } else {
            document.querySelectorAll('[data-ppt-doc-anchor]').forEach((anchor) => {
              const docNum = anchor.getAttribute('data-ppt-doc');
              const record = currentProjectRecords[docNum];
              if (record) {
                renderBadgeForAnchor(anchor, record, false);
              }
            });
          }
        }
      });
    } catch (e) {}
  }

  const badgeRefreshInterval = setInterval(() => {
    if (!isExtensionValid()) {
      clearInterval(badgeRefreshInterval);
      return;
    }
    document.querySelectorAll('[data-ppt-doc-anchor]').forEach((anchor) => {
      const docNum = anchor.getAttribute('data-ppt-doc');
      const record = currentProjectRecords[docNum];
      if (record) {
        renderBadgeForAnchor(anchor, record, false);
      }
    });
  }, 60000);

  function scheduleDetailPageRetry() {
    let attempts = 0;
    const maxAttempts = 15;
    const interval = setInterval(() => {
      attempts++;
      if (isViewingPatentDetail()) {
        const badge = document.querySelector('.ppt-badge-main');
        if (!badge) {
          handleDetailPageSingleMainPatent();
        } else {
          clearInterval(interval);
        }
      } else {
        clearInterval(interval);
      }
      if (attempts >= maxAttempts) {
        clearInterval(interval);
      }
    }, 300);
  }

  function init() {
    initStorage(() => {
      currentQueryKey = getSearchQueryKey();
      renderProjectBannerAndPill();
      updateBannerAndPillUI();
      setupRouteListeners();
      setupSearchActionInterception();

      setupIntersectionObserver();
      setupGlobalClickCapture();
      scanPage();

      if (isViewingPatentDetail()) {
        scheduleDetailPageRetry();
      }

      // 核心补充：监听视口与局部容器滚动，动态捕获虚拟列表中新滚入的专利节点
      let scrollScanTimer = null;
      const handleScrollScan = () => {
        if (scrollScanTimer) clearTimeout(scrollScanTimer);
        scrollScanTimer = setTimeout(() => {
          scanPage();
        }, 150);
      };

      window.addEventListener('scroll', handleScrollScan, { passive: true });
      document.addEventListener('scroll', handleScrollScan, { passive: true, capture: true });

      const mutationObserver = new MutationObserver((mutations) => {
        let hasForeignNodes = false;
        for (const m of mutations) {
          if (m.target && (m.target.classList?.contains('ppt-badge') || m.target.closest?.('.ppt-badge'))) {
            continue;
          }
          if (m.addedNodes && m.addedNodes.length > 0) {
            for (const node of m.addedNodes) {
              if (node.nodeType === Node.ELEMENT_NODE) {
                if (node.classList?.contains('ppt-badge')) continue;
                if (node.querySelector && node.querySelector('.ppt-badge')) continue;
                hasForeignNodes = true;
                break;
              }
            }
          }
          if (hasForeignNodes) break;
        }

        if (hasForeignNodes) {
          scanPage();
        }
      });

      mutationObserver.observe(document.body, {
        childList: true,
        subtree: true
      });
    });
  }

  window.__ppt_state = {
    get isConfirmed() { return isSessionConfirmed; },
    get confirmedKey() { return confirmedQueryKey; },
    get currentKey() { return currentQueryKey; },
    checkQueryAndSession,
    updateBannerAndPillUI
  };

  window.__ppt_debug = function () {
    console.group('🔍 [智慧芽追踪助手 - 自检报告]');
    console.log('当前页面类型:', isViewingPatentDetail() ? '详情页 (Detail View)' : '列表页 (Search Result)');
    console.log('追踪开关:', config.isTrackingEnabled ? '运行中' : '已暂停');
    console.log('当前项目 ID:', config.currentProjectId);
    console.log('当前项目已存记录数:', Object.keys(currentProjectRecords).length);
    console.log('列表页绑定公开号数:', document.querySelectorAll('[data-ppt-doc-anchor]').length);
    console.log('页面上呈现的徽章数:', document.querySelectorAll('.ppt-badge').length);
    if (isViewingPatentDetail()) {
      const main = document.querySelector('[data-ppt-main-title]');
      console.log('详情页主公开号元素:', main ? main.textContent.trim() : '未标定');
      console.log('主徽章元素:', document.querySelector('.ppt-badge-main'));
    }
    console.groupEnd();
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
