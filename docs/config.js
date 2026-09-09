// GitHub Pages（PKCE モード）で使う設定。
//
// Client ID は PKCE では公開前提の値なので、ここに書いてコミットして問題ありません。
// Client Secret は絶対にここへ書かないでください。PKCE では使いません。
//
// ローカルの Flask 経由で開いた場合、この値は参照されません。
window.SPOTIFY_CONFIG = {
  clientId: "9c3b30ac222f49e1914a1050e36da25a",
};
