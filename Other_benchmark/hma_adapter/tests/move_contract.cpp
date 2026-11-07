#include "eval.h"
#include <cassert>
#include <cmath>
#include <iostream>
long call_count_move_eval=0;
double mean_duration_move_eval=0.0,mean_route_len=0.0;
clock_t find_best_time=0,find_bks_time=0;
int find_best_run=0,find_best_gen=0,find_bks_run=0,find_bks_gen=0;
bool find_better=false;
Data load(char *path){ArgumentParser p;p.addArgument("--problem",1,false);p.addArgument("--benchmark_reference");char x[]="test",a[]="--problem",b[]="--benchmark_reference";char *args[]={x,a,path,b};p.parse(4,args);return Data(p);}
Route route(Data &d,std::vector<int> nodes){Route r(d);r.node_list=nodes;r.update(d);r.total_cost=r.cal_cost(d);return r;}
void check(bool condition,const char *label){if(!condition){std::cerr<<label<<"\n";std::abort();}}
int main(int argc,char **argv){
 assert(argc==4);
 for(int input=1;input<4;++input){
  Data d=load(argv[input]);d.O_1_evl=true;
  Solution two(d);auto a=route(d,{0,1,0}),b=route(d,{0,2,0});two.append(a);two.append(b);two.cal_cost(d);
  Solution one(d);auto c=route(d,{0,1,2,0});one.append(c);one.cal_cost(d);
  double relaxed=-1;
  Move merge;merge.r_indice[0]=0;merge.r_indice[1]=1;merge.len_1=2;merge.seqList_1[0]={0,0,1};merge.seqList_1[1]={1,1,2};merge.len_2=2;merge.seqList_2[0]={1,0,0};merge.seqList_2[1]={1,2,2};
  bool improves=eval_move(two,merge,d,relaxed);
  check(std::abs(merge.delta_cost-(one.cost-two.cost))<1e-8,"weighted merge delta");
  check(improves==(merge.delta_cost<-PRECISION),"weighted merge acceptance");
  Move split;split.r_indice[0]=0;split.r_indice[1]=-1;split.len_1=2;split.seqList_1[0]={0,0,1};split.seqList_1[1]={0,3,3};split.len_2=2;split.seqList_2[0]={-1,0,0};split.seqList_2[1]={0,2,3};
  improves=eval_move(one,split,d,relaxed);
  check(std::abs(split.delta_cost-(two.cost-one.cost))<1e-8,"weighted split delta");
  check(improves==(split.delta_cost<-PRECISION),"weighted split acceptance");
  // A copied vector has no spare capacity: splitting must safely append a route.
  // Earlier tests only evaluated deltas and never exercised apply_move's append.
  Solution copied=one;
  auto split_changed=apply_move(copied,split,d);
  check(copied.len()==2,"copied solution split append");
  check(copied.get(0).node_list==std::vector<int>({0,1,0}),"split first route after reallocation");
  check(copied.get(1).node_list==std::vector<int>({0,2,0}),"split appended route");
  copied.cal_cost(d);check(std::abs(copied.cost-two.cost)<1e-8,"applied split cost");
  apply_move(copied,merge,d);copied.cal_cost(d);
  check(std::abs(copied.cost-one.cost)<1e-8,"applied merge cost");
  Move reverse;reverse.r_indice[0]=0;reverse.r_indice[1]=-2;reverse.len_1=3;reverse.seqList_1[0]={0,0,0};reverse.seqList_1[1]={0,2,1};reverse.seqList_1[2]={0,3,3};
  auto reversed=route(d,{0,2,1,0});improves=eval_move(one,reverse,d,relaxed);
  check(std::abs(reverse.delta_cost-(reversed.cal_cost(d)-one.cost))<1e-8,"directed reversal delta");
  std::cout<<"PASS "<<d.problem_name<<" merge/split/reverse delta, weighted accept, copied split append and merge\n";
 }
}
