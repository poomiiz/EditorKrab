use std::path::Path;
#[tokio::main]
async fn main(){
 let args:Vec<_>=std::env::args().collect();
 let state:serde_json::Value=serde_json::from_str(&std::fs::read_to_string(&args[1]).unwrap()).unwrap();
 let voice=&state["document"]["voice"][&args[2]];
 let people:Vec<editorkrab::faces::Face>=voice["faces"].as_array().unwrap().iter().map(|v|editorkrab::faces::Face{cx:v["cx"].as_f64().unwrap(),cy:v["cy"].as_f64().unwrap(),size:v["size"].as_f64().unwrap()}).collect();
 let speech:Vec<_>=voice["speech"].as_array().unwrap().iter().map(|v|(v[0].as_u64().unwrap(),v[1].as_u64().unwrap())).collect();
 let (frames,w,h)=editorkrab::faces::frames(Path::new(&args[3]),Path::new(&args[4])).await.unwrap();
 let motion=editorkrab::faces::mouth_motion(&people,&frames,w,h);
 let seen:Vec<_>=editorkrab::faces::visual_turns(&motion,&speech).into_iter().map(|(a,b,p)|serde_json::json!({"start_ms":a,"end_ms":b,"person":p})).collect();
 std::fs::write(&args[5],serde_json::to_vec_pretty(&seen).unwrap()).unwrap();
 println!("visual evidence: {} turns / {} frames",seen.len(),frames.len());
}
