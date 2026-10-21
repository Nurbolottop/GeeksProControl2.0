/* Единый модал подтверждения вместо системного confirm()/prompt().
 *
 * Использование на форме:
 *   data-confirm="Текст вопроса"              — обычное подтверждение
 *   data-confirm-input="1.15"                 — требует ввести это значение
 *                                               (попадает в input[name=confirm_code])
 *   data-confirm-ok="Удалить"                 — подпись кнопки (необязательно)
 *   data-confirm-reasons='[["self","Сам ушёл"], ...]'
 *                                              — необязательный выпадающий список причины,
 *                                                выбор попадает в input[name=left_reason]
 */
(function () {
  'use strict';

  var DANGER_RE = /удал|убрать|снять|расформ|безвозвратно|пропад|отозв/i;

  var modal, titleEl, textEl, inputWrap, inputEl, inputHint, errorEl,
      reasonWrap, reasonSelect, okBtn, cancelBtn;
  var activeForm = null;
  var activeSubmitter = null;
  var lastFocused = null;

  function build() {
    modal = document.createElement('div');
    modal.className = 'cmodal';
    modal.innerHTML =
      '<div class="cmodal__overlay"></div>' +
      '<div class="cmodal__card" role="dialog" aria-modal="true" aria-labelledby="cmodal-title">' +
      '  <div class="cmodal__icon" aria-hidden="true"></div>' +
      '  <h3 class="cmodal__title" id="cmodal-title">Подтвердите действие</h3>' +
      '  <p class="cmodal__text"></p>' +
      '  <div class="cmodal__input-wrap" hidden>' +
      '    <label class="cmodal__label">Введите <b class="cmodal__code"></b> для подтверждения</label>' +
      '    <input type="text" class="cmodal__input" autocomplete="off" spellcheck="false">' +
      '    <div class="cmodal__error" hidden>Код не совпадает</div>' +
      '  </div>' +
      '  <div class="cmodal__reason-wrap" hidden>' +
      '    <label class="cmodal__label">Причина (необязательно)</label>' +
      '    <select class="cmodal__select"></select>' +
      '  </div>' +
      '  <div class="cmodal__actions">' +
      '    <button type="button" class="btn btn--secondary cmodal__cancel">Отмена</button>' +
      '    <button type="button" class="btn cmodal__ok">Подтвердить</button>' +
      '  </div>' +
      '</div>';
    document.body.appendChild(modal);

    titleEl = modal.querySelector('.cmodal__title');
    textEl = modal.querySelector('.cmodal__text');
    inputWrap = modal.querySelector('.cmodal__input-wrap');
    inputEl = modal.querySelector('.cmodal__input');
    inputHint = modal.querySelector('.cmodal__code');
    errorEl = modal.querySelector('.cmodal__error');
    reasonWrap = modal.querySelector('.cmodal__reason-wrap');
    reasonSelect = modal.querySelector('.cmodal__select');
    okBtn = modal.querySelector('.cmodal__ok');
    cancelBtn = modal.querySelector('.cmodal__cancel');

    modal.querySelector('.cmodal__overlay').addEventListener('click', close);
    cancelBtn.addEventListener('click', close);
    okBtn.addEventListener('click', accept);
    inputEl.addEventListener('input', validateInput);
    inputEl.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); accept(); }
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && modal.classList.contains('is-open')) close();
    });
  }

  function validateInput() {
    errorEl.hidden = true;
    var expected = activeForm ? (activeForm.dataset.confirmInput || '') : '';
    okBtn.disabled = Boolean(expected) && inputEl.value.trim() !== expected;
  }

  function open(form, submitter) {
    if (!modal) build();
    activeForm = form;
    activeSubmitter = submitter || null;
    lastFocused = document.activeElement;

    var message = form.dataset.confirm || 'Вы уверены?';
    var expected = form.dataset.confirmInput || '';
    var danger = Boolean(expected) || DANGER_RE.test(message);

    textEl.textContent = message;
    titleEl.textContent = danger ? 'Подтвердите удаление' : 'Подтвердите действие';
    okBtn.textContent = form.dataset.confirmOk || (danger ? 'Удалить' : 'Подтвердить');
    okBtn.classList.toggle('btn--danger-solid', danger);
    modal.classList.toggle('cmodal--danger', danger);

    inputWrap.hidden = !expected;
    errorEl.hidden = true;
    if (expected) {
      inputHint.textContent = expected;
      inputEl.value = '';
      okBtn.disabled = true;
    } else {
      okBtn.disabled = false;
    }

    var reasonsRaw = form.dataset.confirmReasons || '';
    reasonWrap.hidden = !reasonsRaw;
    if (reasonsRaw) {
      var reasons = JSON.parse(reasonsRaw);
      var options = '<option value="">Без причины</option>';
      reasons.forEach(function (pair) {
        options += '<option value="' + pair[0] + '">' + pair[1] + '</option>';
      });
      reasonSelect.innerHTML = options;
    }

    modal.classList.add('is-open');
    document.body.classList.add('cmodal-open');
    setTimeout(function () { (expected ? inputEl : okBtn).focus(); }, 30);
  }

  function close() {
    modal.classList.remove('is-open');
    document.body.classList.remove('cmodal-open');
    activeForm = null;
    activeSubmitter = null;
    if (lastFocused && lastFocused.focus) lastFocused.focus();
  }

  function accept() {
    if (!activeForm) return close();
    var expected = activeForm.dataset.confirmInput || '';
    if (expected) {
      if (inputEl.value.trim() !== expected) {
        errorEl.hidden = false;
        inputEl.focus();
        return;
      }
      var codeField = activeForm.querySelector('[name=confirm_code]');
      if (codeField) codeField.value = inputEl.value.trim();
    }
    if (!reasonWrap.hidden) {
      var reasonField = activeForm.querySelector('[name=left_reason]');
      if (reasonField) reasonField.value = reasonSelect.value;
    }
    var form = activeForm;
    var submitter = activeSubmitter;
    form.dataset.confirmed = '1';
    close();
    if (submitter && form.requestSubmit) {
      form.requestSubmit(submitter);
    } else if (form.requestSubmit) {
      form.requestSubmit();
    } else {
      form.submit();
    }
  }

  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (!form.dataset.confirm && !form.dataset.confirmInput) return;
    if (form.dataset.confirmed === '1') {
      delete form.dataset.confirmed;
      return; // подтверждено в модале — пропускаем
    }
    e.preventDefault();
    open(form, e.submitter);
  }, true);
})();
