use shortener_rs::{app, db::Db};

#[tokio::main]
async fn main() {
    let db_path = std::env::var("SHORTENER_DB").unwrap_or_else(|_| ":memory:".into());
    let admin_token = std::env::var("SHORTENER_ADMIN_TOKEN")
        .unwrap_or_else(|_| "dev-admin".into());
    let port: u16 = std::env::var("SHORTENER_PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(8788);
    let db = Db::open(&db_path).expect("database open + migrate");
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", port))
        .await
        .expect("bind");
    println!("shortener-rs listening on http://127.0.0.1:{port} (db: {db_path})");
    axum::serve(listener, app(db, &admin_token)).await.expect("serve");
}
