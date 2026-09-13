"""Access-code transport uses synthetic credentials, with no external requests."""
import unicodedata
from urllib.parse import quote

import pytest

from backend.tests.test_v2_personal_import_ui import browser, ui, playwright


@pytest.mark.parametrize('code', ['테스트입장', 'short-demo', 'legacy-ASCII-01234567890123456789+/=',
                                 unicodedata.normalize('NFD', '테스트입장')])
def test_code_login_has_ascii_header_and_logout_clears_credential(ui, code):
    page = ui()
    normalized = unicodedata.normalize('NFC', code)
    wire = quote(normalized, safe="~()*!.'-_") if not normalized.isascii() else normalized
    page.evaluate("""expected => {
      document.querySelector('#personal-access-panel').hidden = false;
      window.__authHeaders = [];
      const previous = window.fetch;
      window.fetch = (path, options = {}) => {
        const auth = new Headers(options.headers).get('Authorization');
        window.__authHeaders.push(auth);
        if (auth !== 'Bearer ' + expected) return Promise.resolve(new Response('{}', {status: 401}));
        return previous(path, options);
      };
    }""", wire)
    page.locator('#personal-access-code').fill(code)
    page.locator('#personal-access-submit').click()
    playwright.expect(page.locator('#personal-access-panel')).to_be_hidden()
    playwright.expect(page.locator('#personal-access-code')).to_have_value('')
    assert page.evaluate('window.__authHeaders') == ['Bearer ' + wire]
    assert page.evaluate('localStorage.length + sessionStorage.length') == 0
    page.locator('#personal-logout').click()
    playwright.expect(page.locator('#personal-access-panel')).to_be_visible()
    page.locator('#personal-access-code').fill('틀린코드')
    page.locator('#personal-access-submit').click()
    playwright.expect(page.locator('#personal-access-error')).to_contain_text('맞지 않습니다')
    assert page.evaluate('window.__authHeaders.at(-1)') == 'Bearer ' + quote('틀린코드')
