package main

func main() {
	ch := make(chan int) // unbuffered: no receiver ever arrives
	ch <- 1
}
