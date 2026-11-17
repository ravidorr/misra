  <div class="account" id="account" hidden>
    <button type="button" class="btn btn-pill" id="signIn" aria-haspopup="dialog">Sign in<span class="sr-only"> with Google</span></button>
    <div class="account-user" id="accountUser" hidden>
      <button type="button" class="btn btn-pill btn-icon" id="accountMenuButton" aria-label="Account" title="Account" aria-expanded="false" aria-controls="accountMenu">
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
      </button>
      <div class="account-menu" id="accountMenu" hidden>
        <p class="account-email" id="accountEmail"></p>
        <!--digest--><button type="button" class="account-item" id="digestOpen" aria-haspopup="dialog" data-digest="open" data-digest-state="loading">Job alerts</button><!--/digest-->
        <button type="button" class="account-item" id="signOut">Sign out</button>
        <button type="button" class="account-item account-danger" id="deleteAccount" aria-haspopup="dialog">Delete my account</button>
      </div>
    </div>
  </div>
