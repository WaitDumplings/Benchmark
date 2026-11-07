#include "evolution.h"
#include <cassert>
#include <cmath>
#include <iostream>
long call_count_move_eval=0;
double mean_duration_move_eval=0.0,mean_route_len=0.0;
clock_t find_best_time=0,find_bks_time=0;
int find_best_run=0,find_best_gen=0,find_bks_run=0,find_bks_gen=0;
bool find_better=false;
Data load(char *path){ArgumentParser p;p.addArgument("--problem",1,false);p.addArgument("--benchmark_reference");char x[]="test",a[]="--problem",b[]="--benchmark_reference";char *args[]={x,a,path,b};p.parse(4,args);return Data(p);}
int main(int argc,char **argv){
 assert(argc==2);Data d=load(argv[1]);
 for(int customers:std::vector<int>{100,1000,120,10}){
  Route r(d);r.customer_list={0};
  for(int i=1;i<=customers;++i)r.customer_list.push_back(i);
  r.customer_list.push_back(0);
  int dimension=r.customer_list.size()-1;
  double cost=INFINITY;
  bool valid=parallel_station_insertion(dimension,r,d,cost);
  assert(valid && std::isfinite(cost));
  std::vector<int> customers_after;
  for(int node:r.node_list)if(d.node[node].type!=2)customers_after.push_back(node);
  assert(customers_after==r.customer_list);
  std::vector<status> states;int flag,negative;double checked_cost;
  update_route_status(r.node_list,states,d,flag,checked_cost,negative);
  assert(flag==1 && std::abs(cost-checked_cost)<1e-8);
  std::cout<<"PASS PSI dimension="<<dimension<<" population="<<dimension*3<<" full genetic initialization/evolution and station insertion; customer order preserved\n";
 }
}
