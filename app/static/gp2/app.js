/*
 * GeeksPro Control 2.0 — поведение интерфейса.
 *
 * Подключается ДО Alpine (оба с defer): компоненты регистрируются на
 * событии alpine:init. Без сборщика и зависимостей, кроме htmx и Alpine,
 * которые уже есть в проекте.
 *
 *   gpShell     — состояние каркаса: мобильная панель, свёрнутый сайдбар, тема
 *   gpDropdown  — выпадающее меню (Escape, клик вне, стрелки)
 *   gpTabs      — вкладки на клиенте (для вкладок-ссылок не нужен)
 *   GP.toast()  — всплывающее уведомление; с сервера — HX-Trigger {"gp:toast": …}
 *   [data-gp-open="id"] / [data-gp-close] — модальные окна и панели на <dialog>
 *   [data-gp-copy="id"] — копировать текст/ссылку элемента с этим id в буфер
 */
(function () {
  'use strict';

  var THEME_KEY = 'gp2-theme';
  var SIDEBAR_KEY = 'gp2-sidebar-collapsed';

  function read(key) {
    try { return window.localStorage.getItem(key); } catch (e) { return null; }
  }
  function write(key, value) {
    try {
      if (value === null) window.localStorage.removeItem(key);
      else window.localStorage.setItem(key, value);
    } catch (e) { /* приватный режим — просто не запоминаем */ }
  }

  /* ---------------- Тема ---------------- */
  var theme = {
    get: function () {
      var value = read(THEME_KEY);
      return value === 'light' || value === 'dark' ? value : 'system';
    },
    set: function (value) {
      var root = document.documentElement;
      if (value === 'light' || value === 'dark') {
        root.setAttribute('data-theme', value);
        write(THEME_KEY, value);
      } else {
        root.removeAttribute('data-theme');
        write(THEME_KEY, null);
        value = 'system';
      }
      document.dispatchEvent(new CustomEvent('gp:theme', { detail: { theme: value } }));
      return value;
    },
    isDark: function () {
      var value = theme.get();
      if (value === 'system') return window.matchMedia('(prefers-color-scheme: dark)').matches;
      return value === 'dark';
    },
  };

  /* ---------------- Toast ---------------- */
  var ICONS = { info: 'info', success: 'check-circle', warning: 'alert-triangle', danger: 'alert-circle' };
  var LEVELS = { error: 'danger', danger: 'danger', warning: 'warning', success: 'success', info: 'info', debug: 'info' };

  function spriteUrl() {
    var root = document.querySelector('[data-gp-sprite]');
    return root ? root.getAttribute('data-gp-sprite') : '';
  }

  function dismiss(el) {
    if (!el || el.classList.contains('is-leaving')) return;
    el.classList.add('is-leaving');
    window.setTimeout(function () { el.remove(); }, 220);
  }

  function schedule(el) {
    var level = el.getAttribute('data-level') || 'info';
    var timeout = level === 'danger' ? 10000 : 6000;
    window.setTimeout(function () { dismiss(el); }, timeout);
  }

  function toast(message, level) {
    level = LEVELS[level] || 'info';
    var region = document.getElementById('gp-toasts');
    if (!region || !message) return;
    var el = document.createElement('div');
    el.className = 'gp-toast gp-toast--' + level;
    el.setAttribute('role', level === 'danger' ? 'alert' : 'status');
    el.setAttribute('data-level', level);
    var sprite = spriteUrl();
    el.innerHTML =
      '<svg class="gp-icon" aria-hidden="true"><use href="' + sprite + '#' + ICONS[level] + '"></use></svg>' +
      '<div class="gp-toast__text"></div>' +
      '<button type="button" class="gp-btn gp-btn--ghost gp-btn--icon gp-btn--sm" data-gp-toast-close aria-label="Закрыть">' +
      '<svg class="gp-icon gp-icon--sm" aria-hidden="true"><use href="' + sprite + '#x"></use></svg></button>';
    el.querySelector('.gp-toast__text').textContent = message;
    region.appendChild(el);
    schedule(el);
  }

  /* ---------------- Модальные окна и панели ---------------- */
  function openDialog(id) {
    var dialog = document.getElementById(id);
    if (dialog && typeof dialog.showModal === 'function' && !dialog.open) dialog.showModal();
  }

  document.addEventListener('click', function (event) {
    var opener = event.target.closest('[data-gp-open]');
    if (opener) {
      event.preventDefault();
      openDialog(opener.getAttribute('data-gp-open'));
      return;
    }
    var closer = event.target.closest('[data-gp-close]');
    if (closer) {
      var dialog = closer.closest('dialog');
      if (dialog) dialog.close();
      return;
    }
    var copier = event.target.closest('[data-gp-copy]');
    if (copier) {
      var source = document.getElementById(copier.getAttribute('data-gp-copy'));
      var text = source ? (source.href || source.value || source.textContent || '').trim() : '';
      if (text && navigator.clipboard) {
        navigator.clipboard.writeText(text).then(function () { toast('Скопировано', 'success'); }, function () {});
      }
      return;
    }
    var toastClose = event.target.closest('[data-gp-toast-close]');
    if (toastClose) {
      dismiss(toastClose.closest('.gp-toast'));
      return;
    }
    // Клик по подложке <dialog> (вне панели) закрывает окно
    if (event.target.tagName === 'DIALOG' && event.target.open && event.target.hasAttribute('data-gp-dismissable')) {
      event.target.close();
    }
  });

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('#gp-toasts .gp-toast').forEach(schedule);
  });

  /* ---------------- htmx ---------------- */
  document.addEventListener('gp:toast', function (event) {
    var detail = event.detail || {};
    toast(detail.message || detail.value, detail.level);
  });
  document.addEventListener('gp:open-modal', function (event) {
    openDialog((event.detail || {}).id || (event.detail || {}).value);
  });
  document.addEventListener('htmx:afterSwap', function (event) {
    var target = event.detail && event.detail.target;
    var dialog = target && target.closest && target.closest('dialog[data-gp-autoopen]');
    if (dialog) openDialog(dialog.id);
  });
  document.addEventListener('htmx:responseError', function (event) {
    var status = event.detail && event.detail.xhr ? event.detail.xhr.status : 0;
    toast(status === 403 ? 'Недостаточно прав для этого действия.' : 'Не удалось выполнить действие. Попробуйте ещё раз.', 'danger');
  });
  document.addEventListener('htmx:sendError', function () {
    toast('Нет связи с сервером. Проверьте подключение.', 'danger');
  });

  /* ---------------- Alpine-компоненты ---------------- */
  document.addEventListener('alpine:init', function () {
    var Alpine = window.Alpine;
    var desktop = window.matchMedia('(min-width: 1280px)');

    Alpine.data('gpShell', function () {
      return {
        navOpen: false,
        collapsed: read(SIDEBAR_KEY) === '1',
        theme: theme.get(),
        init: function () {
          var self = this;
          desktop.addEventListener('change', function () { self.navOpen = false; });
          window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function (e) { self.systemDark = e.matches; });
          document.addEventListener('gp:theme', function (e) { self.theme = e.detail.theme; });
        },
        toggleNav: function () { this.navOpen = !this.navOpen; },
        closeNav: function () { this.navOpen = false; },
        toggleCollapsed: function () {
          this.collapsed = !this.collapsed;
          write(SIDEBAR_KEY, this.collapsed ? '1' : null);
        },
        // В режиме «рейки» группа меню сначала разворачивает панель
        expandForGroup: function () {
          var rail = window.matchMedia('(min-width: 768px)').matches && (!desktop.matches || this.collapsed);
          if (rail && !this.navOpen) { this.navOpen = true; return true; }
          return false;
        },
        systemDark: window.matchMedia('(prefers-color-scheme: dark)').matches,
        get dark() { return this.theme === 'dark' || (this.theme === 'system' && this.systemDark); },
        setTheme: function (value) { this.theme = theme.set(value); },
        cycleTheme: function () {
          this.setTheme(theme.isDark() ? 'light' : 'dark');
        },
      };
    });

    Alpine.data('gpDropdown', function () {
      return {
        open: false,
        toggle: function () { this.open ? this.close() : this.show(); },
        show: function () {
          this.open = true;
          var self = this;
          this.$nextTick(function () {
            var first = self.$refs.menu && self.$refs.menu.querySelector('.gp-menu__item');
            if (first) first.focus({ preventScroll: true });
          });
        },
        close: function (focusButton) {
          if (!this.open) return;
          this.open = false;
          if (focusButton && this.$refs.button) this.$refs.button.focus();
        },
        move: function (step) {
          var items = Array.prototype.slice.call(this.$refs.menu.querySelectorAll('.gp-menu__item'));
          if (!items.length) return;
          var index = items.indexOf(document.activeElement);
          items[(index + step + items.length) % items.length].focus();
        },
      };
    });

    Alpine.data('gpTabs', function (initial) {
      return {
        active: initial,
        select: function (key) { this.active = key; },
        move: function (event, step) {
          var tabs = Array.prototype.slice.call(event.currentTarget.parentElement.querySelectorAll('[role="tab"]'));
          var index = tabs.indexOf(event.currentTarget);
          var next = tabs[(index + step + tabs.length) % tabs.length];
          next.focus();
          next.click();
        },
      };
    });
  });

  window.GP = { theme: theme, toast: toast, openDialog: openDialog };
})();
